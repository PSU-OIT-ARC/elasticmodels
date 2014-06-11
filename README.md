# Elasticmodels

Elasticmodels helps you index and query your Django models using elasticsearch.
It is designed to be an alternative to django-haystack when you need more control over
your index creation, and you are always going to use elasticsearch.

# Setup

## settings.py

In your Django settings file, define these variables:

```python

# the URL to your elasticsearch instance
ELASTIC_SEARCH_URL = "http://127.0.0.1:9200/"
# the name of your index in elasticsearch
ELASTIC_SEARCH_INDEX = "foobar"
# a dict of configuration information you want passed to elasticsearch when
# your index is created
ELASTIC_SEARCH_SETTINGS = {
    "settings": {
        "analysis": {
            "analyzer": {
                "snowball": {
                    "type": "snowball",
                    "stopwords": "_none_"
                }
            }
        }
    }
}
```

Add elasticmodels to INSTALLED_APPS:

```python

INSTALLED_APPS = (
    ...
    'elasticmodels',
)
```

## app/search_indexes.py

In a Django app, create a search_indexes.py file, like so:

```python

from elasticmodels import Indexable
from django.template.loader import render_to_string
from .models import File, FileTag

class FileIndex(Indexable):
    # specify the model class this index is for
    model = File

    def mapping(self):
        """
        Return the elasticsearch mapping for this model type
        """
        return {
            "properties": {
                "pk": {"type": "integer", "index": "not_analyzed"},
                "content": {"type": "string", "analyzer": "snowball"},
                "tags": {"type": "string", "analyzer": "keyword"},
                "org_id": {"type": "integer", "index": "not_analyzed"},
                "type": {"type": "integer", "analyzer": "keyword"},
                "uploaded_by_id": {"type": "integer", "analyzer": "keyword"},
            }
        }

    def prepare(self, obj):
        """
        Return obj transformed into a dict that corresponds to the mapping
        you defined. This is what will be indexed by elasticsearch.
        """
        return {
            "pk": obj.pk,
            "content": render_to_string("files/search.txt", {"object": obj}),
            "tags": [ft.tag.name for ft in FileTag.objects.filter(file=obj).select_related("tag")],
            "org_id": obj.org_id,
            "type": obj.type,
            "uploaded_by_id": obj.uploaded_by_id,
        }
```

# Usage

## Deleting and recreating your index

    ./manage.py rebuild_index

**This will delete the entire elasticsearch index** and recreate it. All your
model objects will be re-indexed.

## Adding an individual object to the index

```python

from elasticmodels import make_searchable

f = File(name="Foo", type=1)
f.save()

make_searchable(f)

```

## Querying

Your subclass of elasticmodels.Indexable has a class attribute called `objects`
which returns an elasticutils `S` instance. You can then use whatever methods are
available in elasticutils on the S instance.

See:
http://elasticutils.readthedocs.org/en/latest/searching.html
http://elasticutils.readthedocs.org/en/latest/searching.html#filters-filter
http://elasticutils.readthedocs.org/en/latest/searching.html#queries-query
http://elasticutils.readthedocs.org/en/latest/searching.html#advanced-filters-f-and-filter-raw

```python

from elasticutils import F
from .search_indexes import FileIndex

results = FileIndex.objects.filter(F(type=1) | F(type=2)).query(content__match="foo")
for result in results:
    print result.pk, result.content
```
