"""
Load all the search_indexes modules from all the apps. This has the side effect
of populating the index_registry in search/__init__.py
"""
from django.db.models.loading import get_apps
from . import make_searchable

apps = get_apps()
for app in apps:
    try:
        __import__(app.__package__ + ".search_indexes")
    except (ImportError, TypeError) as e:
        # The package was None, or the search_indexes file doesn't exist
        pass
