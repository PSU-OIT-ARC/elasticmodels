# these are just convenience imports
from .indexes import Index, suspended_updates  # noqa
from .fields import (  # noqa
    StringField,
    FloatField,
    DoubleField,
    ByteField,
    ShortField,
    IntegerField,
    LongField,
    DateField,
    BooleanField,
    TemplateField,
    ObjectField,
    NestedField,
    ListField,
)

default_app_config = 'elasticmodels.apps.ElasticmodelsConfig'
