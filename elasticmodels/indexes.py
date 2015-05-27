import copy
from collections import defaultdict
from contextlib import contextmanager
from itertools import chain
import threading

from six import add_metaclass
from django.db import models
from elasticsearch.helpers import bulk
from elasticsearch_dsl.connections import connections
from elasticsearch_dsl.document import DocTypeMeta
from elasticsearch_dsl.field import Field
from elasticsearch_dsl import DocType

from .exceptions import RedeclaredFieldError, ModelFieldNotMappedError
from .fields import (
    EMField,
    StringField,
    DoubleField,
    ShortField,
    IntegerField,
    LongField,
    DateField,
    BooleanField,
)


# this allows us to queue up index updates (in a thread safe manner)
local_storage = threading.local()


class IndexRegistry:
    """
    This keeps a mapping of models classes to a set of Index instances. It
    is necessary to have this mapping when a model is saved or deleted, so you
    know which indexes to update.
    """
    def __init__(self):
        self.model_to_indexes = defaultdict(set)
        self.connected = False

    def register(self, model, index):
        """Register the model with the registry"""
        self.model_to_indexes[model].add(index)
        if not self.connected:
            connections.index_name = {}
            from django.conf import settings
            kwargs = {}
            for name, params in settings.ELASTICSEARCH_CONNECTIONS.items():
                params = copy.deepcopy(params)
                kwargs[name] = params
                connections.index_name[name] = params.pop("index_name")
            connections.configure(**kwargs)
            self.connected = True

    def update(self, instance, **kwargs):
        """
        Update all the Index instances attached to this model (if their
        ignore_signals flag allows it)
        """
        for index in self.model_to_indexes[instance.__class__]:
            if not index._doc_type.ignore_signals:
                index.update(instance, **kwargs)

    def delete(self, instance, **kwargs):
        """
        Delete the object from all its indexes (with ignore_signals=False)
        """
        self.update(instance, action="delete", **kwargs)

    def get_indexes(self):
        return set(chain(*self.model_to_indexes.values()))

    def get_models(self):
        return self.model_to_indexes.keys()

    def indexes_for_model(self, model):
        return self.model_to_indexes[model]

    def indexes_for_connection(self, using):
        return (index for index in self.get_indexes() if index._doc_type.using == using)


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


class DocTypeProxy(object):
    """
    We want to easily expose the my_index.objects.search().query() and filter()
    methods without having to call search(). So this proxy object exposes those
    methods
    """
    def __init__(self, index):
        self.index = index

    def filter(self, *args, **kwargs):
        return self.index.search().filter(*args, **kwargs)

    def query(self, *args, **kwargs):
        return self.index.search().query(*args, **kwargs)

    def all(self):
        return self.index.search()

    def __getattr__(self, key):
        return getattr(self.index, key)

    def __str__(self):
        return str(self.index._doc_type.name)


class EMDocTypeMeta(DocTypeMeta):
    def __new__(cls, name, bases, attrs):
        super_new = super(EMDocTypeMeta, cls).__new__

        # skip the stuff initialization stuff below for this class itself
        parents = [b for b in bases if isinstance(b, EMDocTypeMeta)]
        if not parents:
            return super_new(cls, name, bases, attrs)

        # to avoid naming a field in Meta.fields and as a class attribute, we
        # generate a set of the field names that are being used
        class_fields = set(name for name, field in attrs.items() if isinstance(field, Field))

        # copy all our extra attributes from the meta class, since the
        # superclass will discard them
        model = attrs['Meta'].model
        model_field_names = getattr(attrs['Meta'], "fields", [])
        date_field = getattr(attrs['Meta'], "date_field", None)
        ignore_signals = getattr(attrs['Meta'], "ignore_signals", False)

        cls = super_new(cls, name, bases, attrs)

        # tack on our extra attributes
        cls._doc_type.model = model
        cls._doc_type.date_field = date_field
        cls._doc_type.ignore_signals = ignore_signals

        # to match Django's API for models, add a class attribute called
        # "objects" that exposes the query() and filter() methods
        cls.objects = DocTypeProxy(cls())
        # Registering the index has the side effect of setting up the ES
        # connections, which populates the index_name attribute on
        # `connections`. That allows us to tack on the index name to the
        # doc_type
        registry.register(model, cls.objects)
        cls._doc_type.index = connections.index_name[cls._doc_type.using]

        # create a lookup lookup table for the model fields, and then construct
        # an elasticsearch field based on the type
        fields = model._meta.fields
        fields_lookup = dict((field.name, field) for field in fields)

        # tack on all the fields that were listed in Meta.fields
        for field_name in model_field_names:
            # this field name is already in use
            if field_name in class_fields:
                raise RedeclaredFieldError("You cannot redeclare the field named '%s' on %s" % (field_name, cls.__name__))

            field_instance = cls.objects.to_field(field_name, fields_lookup[field_name])
            cls._doc_type.mapping.field(field_name, field_instance)

        # provide a shortcut to get the fields on the Index, since accessing
        # them is so convoluted
        cls._doc_type._fields = lambda: cls._doc_type.mapping.properties.properties.to_dict()

        return cls


@add_metaclass(EMDocTypeMeta)
class Index(DocType):
    # we need to provide these methods so the index registry works
    def __eq__(self, other):
        return id(self) == id(other)

    def __hash__(self):
        return id(self)

    @property
    def es(self):
        return connections.get_connection(self._doc_type.using)

    def get_queryset(self, start=None, end=None):
        """
        Return the queryset that should be indexed by this.
        """
        qs = self._doc_type.model._default_manager
        filters = {}

        if self._doc_type.date_field:
            if start:
                filters["%s__gte" % self._doc_type.date_field] = start
            if end:
                filters["%s__lte" % self._doc_type.date_field] = end

        qs = qs.filter(**filters)

        return qs

    def prepare(self, instance):
        """
        Take a model instance, and turn it into a dict that can be serialized
        based on the fields defined on this Index subclass
        """
        data = {}
        # There should be an easier way to get at the mapping's field instances...
        for name, field in self._doc_type._fields().items():
            if not isinstance(field, EMField):
                continue

            # if the field's path hasn't been set to anything useful, set it to
            # the name of the field
            if field._path == []:
                field._path = [name]
            # a hook is provided, similar to a Django Form clean_* method that
            # can override the get_from_instance() behavior of the elasticmodels field type.
            # If a method on this class is called prepare_{field_name}
            # where {field_name} is the name of a field on the Index, it is
            # called *instead* of get_from_instance.
            prep_func = getattr(self, "prepare_" + name, field.get_from_instance)
            data[name] = prep_func(instance)

        return data

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
            '_index': self._doc_type.index,
            '_type': self._doc_type.mapping.doc_type,
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

    def put_mapping(self):
        """
        Create the index and mapping in ES
        """
        index_name = self._doc_type.index

        if not self.es.indices.exists(index=index_name):
            analysis = collect_analysis(self._doc_type.using)
            self.es.indices.create(index=index_name, body={'settings': {'analysis': analysis}})

        return self.es.indices.put_mapping(
            index=index_name,
            doc_type=self._doc_type.mapping.doc_type,
            body=self._doc_type.mapping.to_dict()
        )

    def delete_mapping(self):
        return self.es.indices.delete_mapping(index=self._doc_type.index, doc_type=self._doc_type.mapping.doc_type, ignore=[404])

from .analysis import collect_analysis
