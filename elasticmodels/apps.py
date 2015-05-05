from django.apps import AppConfig, apps
from .indexes import registry


class ElasticmodelsConfig(AppConfig):
    name = "elasticmodels"

    def ready(self):
        # these need to be imported so they get registered
        from .receivers import update_indexes, delete_from_indexes  # noqa

        # construct all the indexes now that the models are finished being
        # contructed
        for model in apps.get_models():
            for index in registry.indexes_for_model(model):
                index.construct()
