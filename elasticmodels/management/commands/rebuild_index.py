from django.core.management import call_command

from .clear_index import Command


class Command(Command):
    def handle(self, *args, **kwargs):
        super().handle(*args, **kwargs)
        if self.confirmed:
            call_command("update_index", *args, **kwargs)
