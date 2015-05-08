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

        return instance


class String(EMField, String):
    pass


class Object(EMField, Object):
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


class Nested(Object, Nested):
    pass


class Date(EMField, Date):
    pass


def List(field):
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
    # this is hacky, but the __setattr__ on DslBase which Field is a subclass
    # of adds every attribute to _params. This creates infinite recurision when
    # to_dict() is called, which is obviously a problem. So we remove the
    # method from _params
    field._params.pop("get_from_instance")

    return field


class Template(String):
    def __init__(self, template_name, **kwargs):
        self._template_name = template_name
        super().__init__(**kwargs)

    def get_from_instance(self, instance):
        context = {'object': instance}
        return render_to_string(self._template_name, context)


# take all the basic fields from elasticsearch-dsl, and make them subclass EMField
for f in FIELDS:
    fclass = _make_dsl_class(EMField, f)
    globals()[fclass.__name__] = fclass
