from optparse import make_option
from django.core.management import call_command
from django.conf import settings
from elasticsearch_dsl.connections import connections

from ...analysis import combined_analysis, is_analysis_compatible, diff_analysis
from .clear_index import Command as ClearIndexCommand

class Command(ClearIndexCommand):
    option_list = ClearIndexCommand.option_list + (
        make_option('--clopen', action="store_true", default='', dest='clopen'),
    )

    def handle(self, *args, **options):
        usings = options.get("using") or settings.ELASTICSEARCH_CONNECTIONS.keys()

        for using in usings:
            # figure out if there is a conflict with the analysis defined in ES
            # and the analysis defined in Python land for this connection
            index_name = settings.ELASTICSEARCH_CONNECTIONS[using]['index_name']
            es = connections.get_connection(using)
            result = is_analysis_compatible(using)
            if result is False:
                if options.get("clopen"):
                    # get the existing analysis setting in ES, and combine
                    # those with the ones defined in Python. Close the index,
                    # update the settings, and re-open it
                    analysis = combined_analysis(using)
                    es.indices.close(index=index_name, ignore=[404])
                    es.indices.put_settings(index=index_name, body={'analysis': analysis}, ignore=[404])
                    es.indices.open(index=index_name, ignore=[404])
                else:
                    self.stderr.write(
                        "The analysis defined in ES and the analysis defined by your Indexes are not compatible. Aborting."
                        "Use --clopen to close the index, update the analysis, and open the index again."
                    )
                    self.stderr.write(diff_analysis(using))
                    exit(1)

        super().handle(*args, **options)
        if self.confirmed:
            call_command("update_index", *args, **options)
