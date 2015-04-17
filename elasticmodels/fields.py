import copy
import inspect
from django.template.loader import render_to_string

from .exceptions import VariableLookupError, UndefinedFieldNameError

class BaseField:
    def get_mapping(self):
        raise NotImplementedError

    def get_from_instance(self, instance):
        raise NotImplementedError


class TypedField(BaseField):
    # `type` is the Elasticsearch field type used when a mapping if created with
    #  this field
    type = "string"

    def __init__(self, attr=None, **kwargs):
        # `self.path` is a list of attributes to lookup on a model instance to
        # generate the value for this field when it is going to index a model
        # object. For example, a path of ['foo', 'bar'] would get the value of
        # model_instance.foo.bar. We generate the list based on the attr
        # parameter, which can be a dotted string "path" like "foo.bar"
        self.path = attr.split(".") if attr else []
        # options is passed on as is to ES when a mapping is generated
        self.options = copy.copy(kwargs)
        self.options['type'] = self.type

        # this is the name to be used for the ES field. If the attr parameter
        # was empty, then the `name` property should be set later
        self._name = self.path[-1] if self.path else None

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        """
        Update the `_name` variable and the `path` if necessary. This property
        will be set by IndexBase when it is updating fields that were defined
        as class attributes on a subclass of `Index`
        """
        self._name = value
        # if the path hasn't been set to anything useful, make it the same as
        # the name field
        if self.path == []:
            self.path.append(value)

        return self._name

    def get_mapping(self):
        """A hook to provide customization of the mapping."""
        if not self.name:
            raise UndefinedFieldNameError("Name for field not defined yet!")

        return self.options

    def get_from_instance(self, instance):
        """
        Given an object to index with ES, return the value that should be put
        into ES for this field
        """
        # walk the attribute path to get the value. Similarly to Django, first
        # try getting the value from a dict, then as a attribute lookup, and
        # then as a list index
        for attr in self.path:
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


class StringField(TypedField):
    type = "string"


class FloatField(TypedField):
    type = "float"


class DoubleField(TypedField):
    type = "double"


class ByteField(TypedField):
    type = "byte"


class ShortField(TypedField):
    type = "short"


class IntegerField(TypedField):
    type = "integer"


class LongField(TypedField):
    type = "long"


class DateField(TypedField):
    type = "date"


class BooleanField(TypedField):
    type = "boolean"


class TemplateField(TypedField):
    type = "string"

    def __init__(self, template_name, **kwargs):
        self.template_name = template_name
        super().__init__(**kwargs)

    def get_from_instance(self, instance):
        context = {'object': instance}
        return render_to_string(self.template_name, context)


class ObjectField(TypedField):
    type = "object"

    def __init__(self, *args, properties, **kwargs):
        super().__init__(*args, properties=properties, **kwargs)
        # from the properties argument, generate the subfields
        self.fields = []
        for name, field in properties.items():
            if isinstance(field, BaseField):
                # the name should be set to the key, so the
                # user doesn't have to redundantly specify the
                # name
                field.name = name
            elif inspect.isclass(field) and issubclass(field, BaseField):
                field = field(attr=name)

            self.fields.append(field)

    def get_from_instance(self, instance):
        obj = super().get_from_instance(instance)
        data = {}

        for field in self.fields:
            data[field.name] = field.get_from_instance(obj)

        return data

    def get_mapping(self):
        mapping = super().get_mapping()
        for field in self.fields:
            mapping['properties'][field.name] = field.get_mapping()
        return mapping


class NestedField(ObjectField):
    type = "nested"


class ListField(BaseField):
    def __init__(self, field):
        self.field = field

    def get_mapping(self):
        return self.field.get_mapping()

    def get_from_instance(self, instance):
        for value in self.field.get_from_instance(instance):
            yield value
