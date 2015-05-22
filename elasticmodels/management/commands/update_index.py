import re
from datetime import timedelta
from optparse import make_option

from django.utils import timezone
from django.forms import DateTimeField
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.conf import settings

from ...indexes import registry
from . import get_models


class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option('--start', action="store", default='', dest='start',
                    help='Index data updated starting with this time.  yyyy-mm-dd[-hh:mm] or [#d][#h][#m][#s]'),
        make_option('--end', action="store", default='', dest='end',
                    help='Index data updated on before this time.  yyyy-mm-dd[-hh:mm] or [#d][#h][#m][#s]'),
        make_option('--using', action="append", dest='using',
                    help="Only touch indexes in this connection from settings.ELASTICSEARCH_CONNECTIONS"),
    )
    args = '<app[.model] app[.model] ...>'
    help = 'Creates and populates the search index.'

    duration_re = re.compile(
        r"^(?:(?P<days>\d+)D)?"
        r"(?:(?P<hours>\d+)H)?"
        r"(?:(?P<minutes>\d+)M)?"
        r"(?:(?P<seconds>\d+)S)?$",
        flags=re.IGNORECASE)

    def parse_date_time(self, input):
        field = DateTimeField()
        try:
            return field.to_python(input)
        except ValidationError:
            pass

        match = self.duration_re.match(input)
        if match:
            kwargs = dict((k, int(v)) for (k, v) in match.groupdict().items() if v is not None)
            return timezone.now() - timedelta(**kwargs)

        raise ValueError("%s could not be interpereted as a datetime" % input)

    def handle(self, *args, **options):
        start = None
        if options.get('start'):
            start = self.parse_date_time(options['start'])

        end = None
        if options.get('end'):
            end = self.parse_date_time(options['end'])

        models = get_models(args)

        usings = options.get("using") or settings.ELASTICSEARCH_CONNECTIONS.keys()

        for using in usings:
            for model in models:
                for index in registry.indexes_for_model(model):
                    if index._doc_type.using == using:
                        self.stdout.write("Putting mapping for %s" % str(index))
                        index.put_mapping()

                        qs = index.get_queryset(start=start, end=end)
                        self.stdout.write("Indexing %d %s objects" % (qs.count(), model.__name__))
                        index.update(qs.iterator())
