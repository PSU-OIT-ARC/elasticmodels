from ...indexes import registry

def get_models(args):
    """
    Parse a list of ModelName, appname or appname.ModelName list, and return
    the list of model classes in the IndexRegistry. If the list if falsy,
    return all the models in the registry.
    """
    if args:
        models = []
        for arg in args:
            match_found = False
            for model in registry.get_models():
                if model._meta.app_label == arg:
                    models.append(model)
                    match_found = True
                elif '%s.%s' % (model._meta.app_label, model._meta.model_name) == arg:
                    models.append(model)
                    match_found = True

            if not match_found:
                raise ValueError("No model or app named %s" % arg)
    else:
        models = registry.get_models()

    return set(models)
