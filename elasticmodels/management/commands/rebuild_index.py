from __future__ import print_function
import elasticsearch
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from ... import es, make_searchable, index_registry, clear_index

class Command(BaseCommand):
    help = "Delete the index, recreate it, and reindex all the documents"

    def handle(self, *args, **options):
        clear_index()

        for model_class, index in index_registry.items():
            model = index.model
            es().indices.put_mapping(
                index=settings.ELASTIC_SEARCH_CONNECTION['indexes'],
                doc_type=index.doc_type,
                body={
                    index.doc_type: index.mapping()
                }
            )

            es().indices.refresh(index=settings.ELASTIC_SEARCH_CONNECTION['indexes'])
            #for obj in model.objects.all():
            for obj in index.get_queryset():
            #for obj in model.objects.filter(**(index.filter_params)):
                print("indexing %s pk = %d" % (obj.__class__.__name__, obj.pk))
                # TODO use a bulk update for this
                make_searchable(obj, refresh=False)
            es().indices.refresh(index=settings.ELASTIC_SEARCH_CONNECTION['indexes'])
