from optparse import make_option
from django.core.management.base import BaseCommand
from django.conf import settings
from ...indexes import registry
from . import get_models


class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option('--using', action="append", dest='using',
                    help="Only touch indexes in this connection from settings.ELASTICSEARCH_CONNECTIONS"),
        make_option('--noinput', action="store_true", default='', dest='noinput')
    )
    args = '<app[.model] app[.model] ...>'
    help = "Deletes the search index for the models"

    def handle(self, *args, **options):
        models = get_models(args)
        noinput = options.get("noinput")
        usings = options.get("using") or settings.ELASTICSEARCH_CONNECTIONS.keys()

        if not noinput:
            response = input("Are you sure you want to delete the index(es)? [n/Y]: ")
        else:
            response = "Y"

        self.confirmed = response.lower() == "y"
        if not self.confirmed:
            return

        for using in usings:
            for model in models:
                for index in registry.indexes_for_model(model):
                    if index._doc_type.using == using:
                        self.stdout.write("Deleting %s" % index)
                        index.delete_mapping()
