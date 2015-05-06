import threading
from itertools import chain
from contextlib import contextmanager

import elasticsearch
from elasticsearch.helpers import bulk
import elasticsearch_dsl as dsl
from collections import defaultdict

from django.conf import settings
from django.db import models
from .fields import (
    BaseField, StringField, DoubleField, ShortField, IntegerField, LongField, DateField, BooleanField
)
from .connections import setup_connections
from .exceptions import RedeclaredFieldError, ModelFieldNotMappedError


# this allows us to queue up index updates (in a thread safe manner)
local_storage = threading.local()

model_field_class_to_field_class = {
    models.AutoField: IntegerField,
    models.BigIntegerField: LongField,
    models.BooleanField: BooleanField,
    models.CharField: StringField,
    models.DateField: DateField,
    models.DateTimeField: DateField,
    models.EmailField: StringField,
    models.FileField: StringField,
    models.FilePathField: StringField,
    # python's float has the same precision as Java's double
    models.FloatField: DoubleField,
    models.ImageField: StringField,
    models.IntegerField: IntegerField,
    models.NullBooleanField: BooleanField,
    models.PositiveIntegerField: IntegerField,
    models.PositiveSmallIntegerField: ShortField,
    models.SlugField: StringField,
    models.SmallIntegerField: ShortField,
    models.TextField: StringField,
    models.TimeField: LongField,
    models.URLField: StringField,
}


class IndexRegistry:
    """
    This keeps a mapping of models classes to a set of Index instances. It
    is necessary to have this mapping when a model is saved or deleted, so you
    know which indexes to update.
    """
    def __init__(self):
        self.model_to_indexes = defaultdict(set)

    def register(self, model, index):
        """Register the model with the registry"""
        self.model_to_indexes[model].add(index)

    def update(self, instance, **kwargs):
        """
        Update all the Index instances attached to this model (if their
        ignore_signals flag allows it)
        """
        for index in self.model_to_indexes[instance.__class__]:
            if not index.ignore_signals:
                index.update(instance, **kwargs)

    def delete(self, instance, **kwargs):
        """
        Delete the object from all its indexes (with ignore_signals=False)
        """
        self.update(instance, action="delete", **kwargs)

    def get_models(self):
        return self.model_to_indexes.keys()

    def indexes_for_model(self, model):
        return self.model_to_indexes[model]


registry = IndexRegistry()


@contextmanager
def suspended_updates():
    """
    This allows you to postpone updates to all the search indexes inside of a with:

        with suspended_updates():
            model1.save()
            model2.save()
            model3.save()
            model4.delete()
    """
    if getattr(local_storage, "bulk_queue", None) is None:
        local_storage.bulk_queue = defaultdict(list)

    try:
        yield
    finally:
        for index, items in local_storage.bulk_queue.items():
            index.bulk(chain(*items))
        local_storage.bulk_queue = None


class IndexOptions:
    """
    This is a simple container for the attributes defined on an Index
    subclass's Meta inner-class. This state is shared between all instances of
    an Index, and should be considered immutable!
    """
    def __init__(self, cls, class_fields=()):
        # these are the model field names used on the "fields" attribute of the
        # Index.Meta class
        self.model_field_names = tuple(getattr(cls, "fields", []))
        # this stores the actual Field instances for all the fields declared on
        # the Index subclass
        self.class_fields = tuple(class_fields)
        self.doc_type = getattr(cls, "doc_type", None)
        # which elasticsearch connection to use
        self.using = getattr(cls, "using", "default")
        # http://www.elastic.co/guide/en/elasticsearch/guide/master/dynamic-mapping.html
        self.dynamic = getattr(cls, "dynamic", "strict")
        # the field to use in generating a queryset to index
        self.date_field = getattr(cls, "date_field", None)


class IndexDescriptor:
    # This class ensures the Index isn't accessible via model instances.
    # For example, Poll.search works, but poll_obj.search raises AttributeError.
    def __init__(self, manager):
        self.manager = manager

    def __get__(self, instance, type=None):
        if instance is not None:
            raise AttributeError("Index isn't accessible via %s instances" % type.__name__)

        return self.manager


class IndexBase(type):
    """
    This metaclass constructs a subclass of Index with the Meta class removed
    and replaced with _meta (of type IndexOptions). It handles initalizing all
    the fields defined as class attributes.
    """
    def __new__(cls, name, bases, attrs):
        super_new = super(IndexBase, cls).__new__

        # ensure initialization is only performed for subclasses of Index
        # (excluding the Index class itself).
        parents = [b for b in bases if isinstance(b, IndexBase)]
        if not parents:
            return super_new(cls, name, bases, attrs)

        # loop through the fields attached to the Index class, add'em to the
        # _meta instance, and update them with the proper field name
        class_fields = []
        for field_name, field_instance in [(a, b) for a, b in attrs.items()]:
            if not isinstance(field_instance, BaseField):
                continue

            class_fields.append(field_instance)
            # update the name and path if they weren't specified
            if not field_instance.name:
                field_instance.name = field_name

            # remove the field from the class
            attrs.pop(field_name)

        # construct our own fancy meta instance based on the Index's Meta class
        meta = IndexOptions(attrs.pop("Meta", object), class_fields=class_fields)

        new_class = super_new(cls, name, bases, attrs)
        setattr(new_class, "_meta", meta)

        return new_class


class Index(metaclass=IndexBase):
    """
    This class works similarly to a Model, Manager, Queryset
    and ModelForm (crazy, I know). The README explains its usage.
    """
    def __init__(self, ignore_signals=False):
        # indicates if the this class has been fully constructed
        self._constructed = False

        # indicates if the update_search_index receiver should update this
        # index automatically when a model instance is saved
        self.ignore_signals = ignore_signals

        # ensure the elasticsearch connections are loaded
        setup_connections()

        self.es = settings.ELASTICSEARCH_CONNECTIONS[self._meta.using]['connection']
        self.index = settings.ELASTICSEARCH_CONNECTIONS[self._meta.using]['INDEX_NAME']
        # this is set in construct()
        self.search = None
        self.model = None
        self.fields = []

    def contribute_to_class(self, model_class, field_name):
        """
        This is called by the model metaclass and allows us to get a
        reference to the model constructing this Index subclass instance
        """
        setattr(model_class, field_name, IndexDescriptor(self))
        self.model = model_class

        registry.register(model_class, self)

    def construct(self):
        """
        This is called by a receiver when the model is done being defined. At
        this point in the program, we can access the model._meta fields, so we
        can finish our initialization
        """
        if self._constructed:
            return

        self._constructed = True

        # populate the fields list. Start off with a copy of the fields defined
        # on the Meta class
        self.fields = [field for field in self._meta.class_fields]
        # create a lookup lookup table for the model fields, and then construct
        # an elasticsearch field based on the type
        try:
            fields = self.model._meta.get_fields()
        except AttributeError:
            fields = self.model._meta.fields
        fields_lookup = dict((field.name, field) for field in fields)
        for field_name in self._meta.model_field_names:
            field_instance = self.to_field(field_name, fields_lookup[field_name])
            self.fields.append(field_instance)

        # check for duplicate field names
        names = set()
        for field in self.fields:
            if field.name in names:
                raise RedeclaredFieldError("You cannot redeclare the field named '%s' on %s" % (field.name, self.__class__.__name__))
            names.add(field.name)

        # set the doc_type
        if self._meta.doc_type is None:
            self.doc_type = "%s_%s" % (self.model._meta.app_label, self.model._meta.model_name)
        else:
            self.doc_type = self._meta.doc_type

        # setup an ES to use.
        self.search = self.get_search()

    def get_queryset(self, start=None, end=None):
        """
        Return the queryset that should be indexed by this.
        """
        qs = self.model._default_manager
        filters = {}

        if self._meta.date_field:
            if start:
                filters["%s__gte" % self._meta.date_field] = start
            if end:
                filters["%s__lte" % self._meta.date_field] = end

        qs = qs.filter(**filters)

        return qs

    def prepare(self, instance):
        """
        Take a model instance, and turn it into a dict that can be serialized
        based on the fields defined on this Index subclass
        """
        data = {}
        for field in self.fields:
            # a hook is provided, similar to a Django Form clean_* method that
            # can override the get_from_instance() behavior of the elasticmodels field type.
            # If a method on this class is called prepare_{field_name}
            # where {field_name} is the name of a field on the Index, it is
            # called *instead* of get_from_instance.
            prep_func = getattr(self, "prepare_" + field.name, field.get_from_instance)
            data[field.name] = prep_func(instance)
        return data

    def get_search(self):
        """Create the elasticsearch DSL instance"""
        s = dsl.Search(using=self.es, index=self.index, doc_type=self.doc_type)
        s = s.index(self.index)
        s = s.doc_type(self.doc_type)
        return s

    def to_field(self, field_name, model_field):
        """
        Returns the elasticsearch field instance appropriate for the model
        field class. This is a good place to hook into if you have more complex
        model field to ES field logic
        """
        try:
            return model_field_class_to_field_class[model_field.__class__](attr=field_name)
        except KeyError:
            raise ModelFieldNotMappedError("Cannot convert model field %s to an Elasticsearch field!" % field_name)

    def get_mapping(self):
        """
        Returns a dict representing this Index as an ES mapping
        """
        properties = dict((field.name, field.get_mapping()) for field in self.fields)
        return {
            'dynamic': self._meta.dynamic,
            'properties': properties
        }

    def put_mapping(self):
        """
        Create the index and mapping in ES
        """
        es = self.es
        body = {}
        index_settings = settings.ELASTICSEARCH_CONNECTIONS[self._meta.using].get("SETTINGS", None)
        if index_settings:
            body['settings'] = index_settings

        try:
            es.indices.create(index=self.index, body=body)
        except elasticsearch.exceptions.RequestError as e:
            if "IndexAlreadyExistsException" not in str(e):
                raise

        es.indices.put_mapping(index=self.index, doc_type=self.doc_type, body=self.get_mapping())

    def delete_mapping(self):
        try:
            self.es.indices.delete_mapping(index=self.index, doc_type=self.doc_type)
        except elasticsearch.exceptions.NotFoundError as e:
            if "IndexMissingException" in str(e):
                pass
            elif "TypeMissingException" in str(e):
                pass
            else:
                raise

    def all(self):
        """Provide a similar API to a Django model"""
        return self.search

    def filter(self, *args, **kwargs):
        """Provide a shortcut to the DSL filter method"""
        return self.all().filter(*args, **kwargs)

    def query(self, *args, **kwargs):
        """Provide a shortcut to the DSL query method"""
        return self.all().query(*args, **kwargs)

    def bulk(self, actions, refresh=True, **kwargs):
        return bulk(client=self.es, actions=actions, refresh=refresh, **kwargs)

    def update(self, thing, refresh=True, action="index", **kwargs):
        """
        Update each document in ES for a model, iterable of models or queryset
        """
        # thing can be a model object, or an iterable of models
        kwargs['refresh'] = refresh
        # wrap the model in an iterable so we don't have to have special cases
        # below
        if isinstance(thing, models.Model):
            thing = [thing]

        operations = [{
            '_op_type': action,
            '_index': self.index,
            '_type': self.doc_type,
            '_id': model.pk,
            # we don't do all the work of preparing a model when we're deleting
            # it
            '_source': self.prepare(model) if action != "delete" else None,
        } for model in thing]

        # if running in the suspended_updates context, we just save the thing
        # for later
        if getattr(local_storage, "bulk_queue", None) is not None:
            local_storage.bulk_queue[self].append(operations)
            # should be flush the bulk_queue at a reasonable point?
            return None
        else:
            # to avoid special cases, we just always use the bulk API
            return self.bulk(operations, **kwargs)

    def delete(self, thing, **kwargs):
        """
        Delete the thing from ES
        """
        self.update(thing, action="delete", **kwargs)
