import six
import elasticutils as eu
import elasticsearch
from django.conf import settings

__version__ = "0.0.2"

es = lambda: eu.get_es(urls=[settings.ELASTIC_SEARCH_URL], index=settings.ELASTIC_SEARCH_INDEX)
s = lambda: eu.S().es(urls=[settings.ELASTIC_SEARCH_URL]).indexes(settings.ELASTIC_SEARCH_INDEX)

# this maps a model.__class__ to its subclass of Indexable
index_registry = {}

def make_searchable(object, refresh=True):
    """
    Indexes a model object. Refreshes the index too, so the object is available
    immediately for searching
    """
    if object.pk is None:
        raise ValueError("You tried to index %r but its PK is None" % obj)

    index = index_registry[object.__class__]

    # see if we should be indexing this object
    # (there might be a slicker way to do this; I wanted to avoid extra db queries)
    is_indexable = True        
    for key, value in index.filter_params.iteritems():
        if object.__dict__[key] != value:
            is_indexable = False

    id = index.id(object)            
    if is_indexable:        
        body = index.prepare(object)
        es().index(index=settings.ELASTIC_SEARCH_INDEX, doc_type=index.doc_type, id=id, body=body)
        if refresh:
            es().indices.refresh(index=settings.ELASTIC_SEARCH_INDEX)
    else:
        if refresh:
            es().delete(index=settings.ELASTIC_SEARCH_INDEX, doc_type=index.doc_type, id=id)
            es().indices.refresh(index=settings.ELASTIC_SEARCH_INDEX)
            

def clear_index():
    """Deletes (if it exists) and recreates the index"""
    try:
        es().indices.delete(index=settings.ELASTIC_SEARCH_INDEX)
    except elasticsearch.exceptions.NotFoundError:
        pass
    es().indices.create(index=settings.ELASTIC_SEARCH_INDEX, body=settings.ELASTIC_SEARCH_SETTINGS)


class IndexableBase(type):
    """
    This metaclass will register its subclasses with the index_registry. It
    also adds an "objects" property to the class which returns an elasticutils
    S instance filtered by the doc_type of the model.
    """
    def __init__(cls, *args, **kwargs):
        super(IndexableBase, cls).__init__(*args, **kwargs)
        # add this class to the registry of indexables
        model_class = getattr(cls, "model", None)
        if model_class is None:
            return

        # are they redefining an index for this model?
        if model_class in index_registry and index_registry[model_class].__class__ != cls:
            raise ValueError("The model class %s already has an index defined by %s. Your class %s is trying to redefine it" % (model_class.__name__, index_registry[model_class].__class__.__name__, cls.__name__))

        index_registry[model_class] = cls()

    @property
    def objects(cls):
        """
        Returns an elasticutils S instance that is pre-filtered based on the
        doctype of the model
        """
        if cls.model == None:
            raise TypeError("Class %s needs to define a 'model' class attribute" % (cls.__name__))
        index = index_registry[cls.model]
        return s().doctypes(index.doc_type)


class Indexable(six.with_metaclass(IndexableBase)):
    """
    Subclasses must specify the model class that this index is for, and
    override mapping() and prepare(obj)
    """
    model = None
    filter_params = {}

    @property
    def doc_type(self):
        return self.model._meta.db_table

    def id(self, obj):
        """
        Returns a (hopefully) unique id to use for the object when it is
        indexed by elasticsearch.
        """
        return "%s.%d" % (self.model._meta.db_table, obj.pk)

    def mapping(self):
        return NotImplementedError()

    def prepare(self):
        return NotImplementedError()
