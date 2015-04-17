from django.db.models.signals import post_save, class_prepared, post_delete
from django.dispatch import receiver

from .indexes import registry


@receiver(post_save)
def update_indexes(sender, **kwargs):
    instance = kwargs['instance']
    registry.update(instance)


@receiver(post_delete)
def delete_from_indexes(sender, **kwargs):
    instance = kwargs['instance']
    registry.delete(instance)


@receiver(class_prepared)
def construct_indexes(sender, **kwargs):
    for index in registry.indexes_for_model(sender):
        index.construct()
