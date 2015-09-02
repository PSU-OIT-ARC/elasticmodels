This is a package that allows indexing of django models using elasticsearch. It
requires django, elasticsearch-py, elasticsearch-dsl and a running instance of
elasticsearch.

# Features:
- Management commands (rebuild_index and update_index, clear_index)
- Django signal receivers on save and delete for keeping ES in sync
- Complex field type support (ObjectField, NestedField, ListField)
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
    
Place this line of code at the **bottom** of the `models.py` file.

```python
from .indexes import * # noqa isort:skip
```

This is required for indexing, but placing it at the top would
cause a circular import.

Now, to make this model work with Elasticsearch, create a subclass of
`elasticmodels.Index` in a file called `indexes.py`:

```python
from elasticmodels import Index, StringField, IntegerField, NestedField
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
    type = StringField(attr="type_to_string")

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
    type = StringField(attr="type_to_string")

    extra_data = NestedField(properties={
        "a_key": StringField(),
        "number": IntegerField(attr="another_key")
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

## Using ListField

If you want to store a list of values for a particular field, wrap the field
with `ListField`:

```python
class Car(models.Model):
    # ... #
    @property
    def some_stuff(self):
        """Generate some extra data to save with the model in ES"""
        return ["alpha", "beta", "gamma"]

class CarIndex(Index):
    # ... #
    some_stuff = ListField(StringField(attr="some_stuff"))

    # ... #
```

## Using prepare_field

Sometimes, you need to do some extra prepping before a field should be saved to
elasticsearch. You can add a `prepare_foo(self, instance)` method to an Index
(where foo is the name of the field), and that will be called when the field
needs to be saved.

```python
class CarIndex(Index):
    # ... #

    foo = StringField()

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

`rebuild_index [--clopen] [--using default --using ...] [--noinput] <app[.model] app[.model] ...>`

Shortcut to clear_index and update_index. It will detect a conflict in your
analyzers. If there is a conflict, it will show a diff of the analysis sections
defined in Python and ES. Use `--clopen` to close the ES index, update the
analysis, and reopen the ES index.

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
instance.

- TemplateField(template_name, \*\*elasticsearch_properties)
- ObjectField(properties, attr=None, \*\*elasticsearch_properties)
- NestedField(properties, attr=None, \*\*elasticsearch_properties)
- ListField(field)

# Analyzers

You can define analyzers and use them on fields:

```python
from elasticmodels import Index, ListField, IntegerField, StringField
from elasticsearch_dsl import analyzer, tokenizer, token_filter

name = analyzer(
    "name",
    # the standard analyzer splits the words nicely by default
    tokenizer=tokenizer("standard"),
    filter=[
        # technically, the standard filter doesn't do anything but we include
        # it anyway just in case ES decides to make use of it
        "standard",
        # obviously, lowercasing the tokens is a good thing
        "lowercase",
        # ngram it up
        token_filter(
            "simple_edge",
            type="nGram",
            min_gram=2,
            max_gram=4
        )
    ]
)


class CarIndex(Index):
    # ... #

    # use the builtin ES keyword analyzer
    foo = StringField(analyzer="keyword")
    # use our fancy analyzer
    name = StringField(analyzer=name)

    # ... #
```

When the mapping is created in ES, the analyzer will be created for you.


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
        # when the .save() or .delete() method is called on a model
        # object, any indexes for that model will automatically update the index
        # in ES. If you don't want that behavior, change this to True
        ignore_signals = False


# Testing

In your settings file, set

    TEST_RUNNER = 'elasticmodels.SearchRunner'

or subclass it with your own test runner. **By default, no data is inserted/updated/deleted by Elasticmodels** because it's slow.

If you need a TestCase that actually hits ES, subclass `elasticmodels.ESTestCase`. For each test, all the indexes are destroyed and recreated. The index names are suffixed with "_test" so your data is not clobbered.

You can test against the results of a search form in the following way:

```python
import elasticmodels
from model_mommy.mommy import make
from django.test import TestCase

from project.foo.models import Foo
from project.foo.forms import FooSearchForm


class FooSearchFormTest(TestCase, elasticmodels.ESTestCase):
    # ... #
    def test_foo_search_form_results(self):
        query = 'foobar'
        model = make(Foo, name=query)
        # if you don't use model mommy, do Foo.objects.create(name=query) instead.

        # Pass data into the form as a dictionary of search criteria.
        form = FooSearchForm({'querystring': query}, user=self.user)
        # Call .search() followed by .execute() to turn the results into a Response object.
        # then make it into a list so the contents can be iterated/indexed.
        results = list(form.search().execute())
        # Now check for correct data.
        self.assertEqual(results[0]['name'], model.name)
    # ... #
```

# Tests:

To run the test suite for Python3

    make test

It is assumed you have Elasticsearch running on localhost:9200. An index will
be used called "elasticmodels-unit-test-db"
