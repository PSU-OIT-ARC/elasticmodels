from types import MethodType
from elasticsearch_dsl.field import Object, Nested, Date, String, FIELDS, Field
from elasticsearch_dsl.utils import _make_dsl_class
from django.template.loader import render_to_string
from .exceptions import VariableLookupError


class EMField(Field):
    def __init__(self, attr=None, **kwargs):
        super().__init__(**kwargs)
        # `self.path` is a list of attributes to lookup on a model instance to
        # generate the value for this field when it is going to index a model
        # object. For example, a path of ['foo', 'bar'] would get the value of
        # model_instance.foo.bar. We generate the list based on the attr
        # parameter, which can be a dotted string "path" like "foo.bar"
        self._path = attr.split(".") if attr else []

    def __setattr__(self, key, value):
        # this is a hack we need to make List fields work, since we need to
        # replace the "get_from_instance" method at runtime.
        if key == "get_from_instance":
            self.__dict__[key] = value
        else:
            super().__setattr__(key, value)

    def get_from_instance(self, instance):
        """
        Given an object to index with ES, return the value that should be put
        into ES for this field
        """
        # walk the attribute path to get the value. Similarly to Django, first
        # try getting the value from a dict, then as a attribute lookup, and
        # then as a list index
        for attr in self._path:
            try: # dict lookup
                instance = instance[attr]
            except (TypeError, AttributeError, KeyError, ValueError, IndexError):
                try: # attr lookup
                    instance = getattr(instance, attr)
                except (TypeError, AttributeError):
                    try:  # list-index lookup
                        instance = instance[int(attr)]
                    except (IndexError,  # list index out of range
                            ValueError,  # invalid literal for int()
                            KeyError,    # current is a dict without `int(bit)` key
                            TypeError):  # unsubscriptable object
                                raise VariableLookupError("Failed lookup for key [%s] in %r" % (attr, instance))

            if callable(instance):
                instance = instance()
            elif instance is None:  # no sense walking down the path any further
                return None

        return instance


class StringField(EMField, String):
    pass


class ObjectField(EMField, Object):
    def get_from_instance(self, instance):
        obj = super().get_from_instance(instance)
        data = {}

        for name, field in self.properties.to_dict().items():
            if not isinstance(field, EMField):
                continue

            # if the field's path hasn't been set to anything useful, set it to
            # the name of the field
            if field._path == []:
                field._path = [name]

            data[name] = field.get_from_instance(obj)

        return data


class NestedField(ObjectField, Nested):
    pass


class DateField(EMField, Date):
    pass


def ListField(field):
    """
    This wraps a field so that when get_from_instance is called, the field's
    values are iterated over
    """
    # alter the original field's get_from_instance so it iterates over the
    # values that the field's get_from_instance() method returns
    original_get_from_instance = field.get_from_instance

    def get_from_instance(self, instance):
        for value in original_get_from_instance(instance):
            yield value

    field.get_from_instance = MethodType(get_from_instance, field)

    return field


class TemplateField(StringField):
    def __init__(self, template_name, **kwargs):
        self._template_name = template_name
        super().__init__(**kwargs)

    def get_from_instance(self, instance):
        context = {'object': instance}
        return render_to_string(self._template_name, context)


# take all the basic fields from elasticsearch-dsl, and make them subclass EMField
for f in FIELDS:
    fclass = _make_dsl_class(EMField, f, suffix="Field")
    globals()[fclass.__name__] = fclass
