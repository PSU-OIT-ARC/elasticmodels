from django.conf import settings
from elasticsearch import Elasticsearch

connections_loaded = False

def setup_connections():
    """
    Create all the elasticsearch instances
    """
    global connections_loaded
    if not connections_loaded:
        for name, params in settings.ELASTICSEARCH_CONNECTIONS.items():
            params['connection'] = Elasticsearch(params['HOSTS'], **params.get('OPTIONS', {}))
        connections_loaded = True
