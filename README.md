This is a package that allows indexing of django models using elasticsearch. It
requires django, elasticsearch-py, elasticsearch-dsl and a running instance of
elasticsearch.

# Features:
- Management commands (rebuild_index and update_index, clear_index)
- Django signal receivers on save and delete for keeping ES in sync
- Complex field type support (Object, Nested, List)
- Based of the features of elasticsearch-dsl

# Quick Start:
Add ‘elasticmodels’ to INSTALLED_APPS

You must define ELASTICSEARCH_CONNECTIONS in your django settings.

For example:
```python
ELASTICSEARCH_CONNECTIONS = {
    'default': {
        'hosts': ['http://localhost:9200',],
        'index_name': 'my_index',
    }
    'fish': {
        'hosts': ['http://example.com:9200',],
        'index_name': 'fish',
    }
}
```

Now consider a model like this:

```python
class Car(models.Model):
    license = models.CharField(primary_key=True)
    color = models.CharField()
    description = models.TextField()
    type = models.IntegerField(choices=[
        (1, "Sedan"),
        (2, "Truck"),
        (4, "SUV"),
    ])
```

To make this model work with Elasticsearch, create a subclass of
`elasticmodels.Index`:

```python
from elasticmodels import Index, String, Integer, Nested
from .models import Car


class CarIndex(Index):
    class Meta:
        model = Car
        fields = [
            'license',
            'color',
            'description',
            'type',
        ]
```

Elasticmodels will automatically setup a mapping in Elasticsearch for the Car
model, where the Elasticsearch fields are derived from the `fields` attribute
on the Meta class.

To create the Elasticsearch index and mappings, use the rebuild_index
management command:

    ./manage.py rebuild_index

Now, when you do something like:

    car = Car(license="PYNERD", color="red", type=1, description="A beautiful car")
    car.save()

The object will be saved in Elasticsearch too (using a signal handler). To get a pre-filtered
Elasticsearch-DSL Search instance, use:

    CarIndex.objects.all()

    # or
    CarIndex.objects.filter("term", color="red")

    # or
    CarIndex.objects.query("match", description="beautiful")

The return value of these method calls is an Elasticsearch-DSL instance.

## Using Different Attributes for Model Fields

Let's say you don't want to store the type of the car as an integer, but as the
corresponding string instead. You need some way to convert the type field on
the model to a string, so we'll just add a method for it:

```python
class Car(models.Model):
    # ... #
    def type_to_string(self):
        """Convert the type field to its string representation (the boneheaded way)"""
        if self.type == 1:
            return "Sedan"
        elif self.type == 2:
            return "Truck"
        else:
            return "SUV"
```

Now we need to tell our Index subclass to use that method instead of just
accessing the `type` field on the model directly. Change the CarIndex to look
like this:

```python
class CarIndex(Index):
    # add a string field to the Elasticsearch mapping called type, the value of
    # which is derived from the model's type_to_string attribute
    type = String(attr="type_to_string")

    class Meta:
        model = Car
        # we removed the type field from here
        fields = [
            'license',
            'color',
            'description',
        ]
```

Of course, we need to rebuild the index `./manage.py rebuild_index` after we
make a change like this.

Now when a Car is saved, to determine the value to use for the "type" field, it
looks up the attribute "type_to_string", sees that it's callable, and calls it
(instead of just accessing `model_instance.type` directly).

## Using Nested and Object Fields

Elasticsearch supports object and nested field types. So does Elasticmodels.

Consider a property like this on our Car model:

```python
class Car(models.Model):
    # ... #
    @property
    def extra_data(self):
        """Generate some extra data to save with the model in ES"""
        return {
            "a_key": "a value",
            "another_key": 5
        }
```

We can add a NestedField or ObjectField to our CarIndex to save this extra_data
to ES.

```python
class CarIndex(Index):
    type = String(attr="type_to_string")

    extra_data = Nested(properties={
        "a_key": String(),
        "number": Integer(attr="another_key")
    })

    class Meta:
        model = Car
        fields = [
            'license',
            'color',
            'description',
        ]
```

When a Car is saved, `model_instance.extra_data` will be looked up/called, and whatever
it returns, will be used as the basis to populate the sub-fields listed in `properties`.

## Using prepare_field

Sometimes, you need to do some extra prepping before a field should be saved to
elasticsearch. You can add a `prepare_foo(self, instance)` method to an Index
(where foo is the name of the field), and that will be called when the field
needs to be saved.

```python
class CarIndex(Index):
    # ... #

    foo = String()

    def prepare_foo(self, instance):
        return " ".join(instance.foos)

    # ... #
```

## Signal Receivers

Elasticmodels watches for the post_save and post_delete signals and updates the
ES index appropriately.

## Suspended Updates

If you're updating a bunch of objects at once, you should use the
suspended_updates context manager so you can more efficently batch process the
ES updates:

```python
from elasticmodels import suspended_updates
with suspended_updates():
    model1.save()
    model2.save()
    model3.save()
    model4.delete()
```

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
If instance['foo'] doesn't raise an exception:
    instance['foo']['bar']
    instance['foo'].bar
    instance['foo'][bar]

else if instance.foo doesn't raise an exception:
    instance.foo['bar']
    instance.foo.bar
    instance.foo[bar]

else if instance[foo] doesn't raise an exception:
    instance[foo]['bar']
    instance[foo].bar
    instance[foo][bar]
```

**Extra keyword arguments are passed directly to elasticsearch when the mapping is
created.**

## Simple Fields

- String(attr=None, \*\*elasticsearch_properties)
- Float(attr=None, \*\*elasticsearch_properties)
- Double(attr=None, \*\*elasticsearch_properties)
- Byte(attr=None, \*\*elasticsearch_properties)
- Short(attr=None, \*\*elasticsearch_properties)
- Integer(attr=None, \*\*elasticsearch_properties)
- Date(attr=None, \*\*elasticsearch_properties)
- Boolean(attr=None, \*\*elasticsearch_properties)

## Complex Fields

`properties` is a dict where the key is a field name, and the value is a field
instance or class.

- Template(template_name, \*\*elasticsearch_properties)
- Object(properties, attr=None, \*\*elasticsearch_properties)
- Nested(properties, attr=None, \*\*elasticsearch_properties)
- List(field)

# Index Meta Options

    class Meta:
        # a list of model field names as strings, which will be included in the
        # ES mapping
        fields = []
        # the mapping name to use for this in elasticsearch. The
        # default is derived from the app and model name
        doc_type = "appname_modelname"
        # the ELASTICSEARCH_CONNECTIONS connection to use for this index
        using = "default"
        # the ES dynamic property to use for the mapping
        # dynamic = "strict" <-- This isn't supported by elaticsearch DSL yet
        # the field to use for management commands when using the `--start` and
        # `--end` options. The default is None.
        date_field = "modified_on"

# Tests:

To run the test suite for Python3

    make test

It is assumed you have Elasticsearch running on localhost:9200. An index will
be used called "elasticmodels-unit-test-db"
