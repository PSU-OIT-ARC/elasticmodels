This is a package that allows indexing of django models using
elasticsearch. It requires django, elasticsearch-py and a running instance of
elasticsearch.

# Features:
- Management commands (rebuild_index and update_index, clear_index)
- Django signal receivers on save and delete for keeping ES in sync
- Complex field type support (ObjectField, NestedField, ListField)

# Usage:
Add ‘elasticmodels’ to INSTALLED_APPS

You must define ELASTICSEARCH_CONNECTIONS in your django settings.

For example:
```python
ELASTICSEARCH_CONNECTIONS = {
    'default': {
        'HOSTS': ['http://localhost:9200',],
        'INDEX_NAME': 'my_index',
    }
    'fish': {
        'HOSTS': ['http://example.com:9200',],
        'INDEX_NAME': 'fish',
    }
}
```

Now consider a model like this:

```python
class Car(models.Model):
    license = models.CharField(primary_key=True)
    color = models.CharField()
    type = models.IntegerField(choices=[
        (1, "Sedan"),
        (2, "Truck"),
        (4, "SUV"),
    ])

    def type_to_string(self):
        """Convert the type field to its string representation (the boneheaded way)"""
        if self.type == 1:
            return "Sedan"
        elif self.type == 2:
            return "Truck"
        else:
            return "SUV"

    @property
    def extra_data(self):
        """Generate some extra data to save with the model in ES"""
        return {
            "a_key": "a value",
            "another_key": 5
        }
```

To make this model work with Elasticsearch, create a subclass of
`elasticmodels.Index`:

```python
from elasticmodels import Index, StringField, IntegerField

class CarIndex(Index):

    # a field in an elasticsearch mapping will be created with the name
    # "type". When the model is saved, the ES field will be populated with the
    # value of calling the "type_to_string" method on the model. Regular
    # (non-callable) attributes work just as well
    type = StringField(attr="type_to_string")

    # This creates a field for the "extra_data" property on the model
    extra_data = NestedField(properties={
        "a_key": StringField,
        "a_different_name": IntegerField(attr="another_key")
    })

    # the inner Meta class is used to define other information about the index
    class Meta:
        # list the fields on your model that you want to include in the index with
        # the elasticsearch field typed guessed automatically based on the model
        # field type
        fields = [
            'license',
            'color',
        ]

        # you can specify which ELASTICSEARCH_CONNECTION to use for this index,
        # the default is "default"
        using = "default"

```

The value indexed by elasticsearch for a particular field can be
overridden by creating a `prepare_foo` method on the Index subclass (where foo is
the name of the field). The method gets passed the model instance.

For example:

```python
class CarIndex(Index):
    # ... #
    some_field = ObjectField(properties={
        'hi': StringField,
    })

    def prepare_some_field(self, instance):
        # this gets called *instead of* ObjectField.get_from_instance(instance)
        return {"hi": instance.other}
```

Back on the Model class, add the CarIndex like you would a manager:

```python
class Car(models.Model):
    # ... #

    search = CarIndex()
```

When you use `Car.search.all()` or `Car.search.filter(**kwargs)` or
`Car.search.query(**kwargs)` you get back an Elasticsearch-DSL search object
prefiltered based on the document type.

# Management Commands

`clear_index [--using default --using ...] [--noinput] <app[.model] app[.model] ...>`

By default, this clears every model index (an Elasticsearch mapping), prompting
before doing it. You can limit which connections and models/apps are affected.

`update_index [--using default --using ...] [--start yyyy-mm-dd] [--end yyyy-mm-dd] [<app[.model] app[.model] ...>`

Update every model index. You can limit the scope of the updates by passing a
start and end date, and/or which models/apps/connections to use.

`rebuild_index [--using default --using ...] [--noinput] <app[.model] app[.model] ...>`

Shortcut to clear_index and update_index.

# Field Classes

Most elasticsearch field types are supported. The `attr` argument is a dotted
"attribute path" which will be looked up on the model using Django template
semantics (dict lookup, attribute lookup, list index lookup). For example
`attr="foo.bar"` will try to fetch the first value that doesn't raise an
exception in this order:

```
instance['foo']
    instance['foo']['bar']
    instance['foo'].bar
    instance['foo'][bar]

instance.foo
    instance.foo['bar']
    instance.foo.bar
    instance.foo[bar]

instance[foo]
    instance[foo]['bar']
    instance[foo].bar
    instance[foo][bar]
```

Extra keyword arguments are passed directly to elasticsearch when the field is
created.

## Simple Fields

- StringField(attr=None, \*\*elasticsearch_properties)
- FloatField(attr=None, \*\*elasticsearch_properties)
- DoubleField(attr=None, \*\*elasticsearch_properties)
- ByteField(attr=None, \*\*elasticsearch_properties)
- ShortField(attr=None, \*\*elasticsearch_properties)
- IntegerField(attr=None, \*\*elasticsearch_properties)
- DateField(attr=None, \*\*elasticsearch_properties)
- BooleanField(attr=None, \*\*elasticsearch_properties)

## Complex Fields

`properties` is a dict where the key is a field name, and the value is a field
instance or class.

- TemplateField(template_name, \*\*elasticsearch_properties)
- ObjectField(properties, attr=None, \*\*elasticsearch_properties)
- NestedField(properties, attr=None, \*\*elasticsearch_properties)
- ListField(field)

# Tests:

To run the test suite for Python3

    make test

It is assumed you have Elasticsearch running on localhost:9200. An index will
be used called "elasticmodels-unit-test-db"
