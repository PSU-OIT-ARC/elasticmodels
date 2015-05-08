class ElasticModelsError(Exception):
    pass


class VariableLookupError(ElasticModelsError):
    pass


class RedeclaredFieldError(ElasticModelsError):
    pass


class ModelFieldNotMappedError(ElasticModelsError):
    pass
