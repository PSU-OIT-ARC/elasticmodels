import os
import tempfile
import datetime
from unittest.mock import Mock, patch
from elasticsearch import Elasticsearch, NotFoundError


from django.db import models, connection
from django.test import TestCase
from django.conf import settings
from django.utils.timezone import utc, now
from django.utils import timezone
from model_mommy.mommy import prepare

from .fields import TypedField, TemplateField, StringField, ObjectField, ListField
from .indexes import Index, IndexOptions, suspended_updates, IndexRegistry
from .exceptions import VariableLookupError, UndefinedFieldNameError, RedeclaredFieldError
from .management.commands.clear_index import Command as ClearCommand
from .management.commands.update_index import Command as UpdateCommand
from .management.commands import get_models


class ESTest(TestCase):
    def setUp(self):
        super().setUp()
        es = Elasticsearch(settings.ELASTICSEARCH_CONNECTIONS['default']['HOSTS'])
        try:
            es.indices.delete(index=settings.ELASTICSEARCH_CONNECTIONS['default']['INDEX_NAME'])
        except NotFoundError as e:
            if "IndexMissingException" not in str(e):
                raise e

class Dummy:
    """
    This allows you to do something like
    d = Dummy()
    d.foo.bar = 1
    """
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, name):
        setattr(self, name, Dummy())
        return getattr(self, name)


class TypedFieldTest(TestCase):
    def test_get_mapping(self):
        field = TypedField("foo")
        self.assertEqual({
            "type": "string",
        }, field.get_mapping())

    def test_get_from_instance(self):
        field = TypedField("alpha.beta.gamma")
        m = Dummy()
        m.alpha.beta.gamma = 1
        self.assertEqual(1, field.get_from_instance(m))

        # test callables along the path
        field = TypedField("alpha.beta.gamma")
        m = Dummy()
        m.alpha.beta = lambda: Dummy(gamma=lambda: 2)
        self.assertEqual(2, field.get_from_instance(m))

        # test dicts along the the path
        field = TypedField("alpha.beta.gamma")
        m = Dummy()
        m.alpha = {"beta": Dummy(gamma=3)}
        self.assertEqual(3, field.get_from_instance(m))

        # test list index along the path
        field = TypedField("alpha.beta.3")
        m = Dummy(alpha=Dummy(beta=["a", "b", "c", "d"]))
        self.assertEqual("d", field.get_from_instance(m))

        # an index that doesn't exist should fail hard
        field = TypedField("alpha.beta.100")
        m = Dummy(alpha=Dummy(beta=["a", "b", "c", "d"]))
        self.assertRaises(VariableLookupError, field.get_from_instance, m)

        # an index that doesn't exist should fail hard
        field = TypedField("alpha.beta.gamma")
        m = Dummy(alpha=1)
        self.assertRaises(VariableLookupError, field.get_from_instance, m)


class ObjectFieldField(TestCase):
    def test_get_mapping(self):
        field = ObjectField("person", properties={
            "first_name": StringField(analyzier="foo"),
            "last_name": StringField
        })

        self.assertEqual({
            "type": "object",
            "properties": {
                "first_name": {"type": "string", "analyzier": "foo"},
                "last_name": {"type": "string"},
            }
        }, field.get_mapping())

    def test_init(self):
        # the properties argument should be required
        self.assertRaises(TypeError, ObjectField)

    def test_get_from_instance(self):
        field = ObjectField("person", properties={
            "first_name": StringField(analyzier="foo"),
            "last_name": StringField
        })

        d = Dummy()
        d.person = Dummy(first_name="foo", last_name="bar")

        self.assertEqual(field.get_from_instance(d), {
            'first_name': "foo",
            "last_name": "bar",
        })


class ListFieldTest(TestCase):
    def test_name_set(self):
        """
        This is a regression test that ensures the Field has a
        `name` attribute
        """
        class CarIndex(Index):
            colors = ListField(StringField)

            def prepare_colors(self, instance):
                # override this since there is no model attribute named "color"
                return ["red", "green", "blue"]

            class Meta:
                fields = ['name']

        class Car(models.Model):
            name = models.CharField(max_length=255)

            search = CarIndex()

        self.assertEqual("colors", Car.search.fields[0].name)

    def test_get_mapping(self):
        field = ListField(StringField("foo"))
        self.assertEqual({
            "type": "string",
        }, field.get_mapping())

    def test_get_from_instance(self):
        d = Dummy()
        d.foo.bar = ['alpha', 'beta', 'gamma']
        field = ListField(StringField("foo.bar"))
        self.assertEqual(list(field.get_from_instance(d)), d.foo.bar)


class TemplateFieldTest(TestCase):
    def test_get_from_instance(self):
        f = tempfile.NamedTemporaryFile()
        f.write(b"{{ object.name }}")
        f.flush()

        with self.settings(TEMPLATE_DIRS=[os.path.normpath(os.path.dirname(f.name))]):
            field = TemplateField(os.path.basename(f.name))
            self.assertEqual(field.get_from_instance({"name": "foo"}), "foo")

        f.close()


class IndexOptionsTest(TestCase):
    def test_defaults(self):
        options = IndexOptions(object)
        self.assertEqual(options.model_field_names, ())
        self.assertEqual(options.class_fields, ())
        self.assertEqual(options.doc_type, None)
        self.assertEqual(options.using, "default")
        self.assertEqual(options.dynamic, "strict")

    def test_settings(self):
        class Meta:
            fields = [
                'alpha',
                'beta'
            ]
            doc_type = "lame"
            using = "test"
            dynamic = "bar"

        options = IndexOptions(Meta)
        self.assertEqual(options.model_field_names, ('alpha', 'beta'))
        self.assertEqual(options.doc_type, "lame")
        self.assertEqual(options.using, "test")
        self.assertEqual(options.dynamic, "bar")


class IndexTest(ESTest):
    def setUp(self):
        super().setUp()

        # create a dummy index and model to play with
        class CarIndex(Index):
            color = StringField()

            def prepare_color(self, instance):
                # override this since there is no model attribute named "color"
                return "blue"

            class Meta:
                fields = ['name']

        class Car(models.Model):
            name = models.CharField(max_length=255)

            search = CarIndex()

        self.CarIndex = CarIndex
        self.Car = Car

    def test_model_class_added(self):
        self.assertEqual(self.Car.search.model, self.Car)

    def test_cannot_access_index_from_model_class(self):
        car = self.Car()
        self.assertRaises(AttributeError, lambda: car.search)

    def test_fields_populated(self):
        self.assertEqual(list(field.name for field in self.Car.search.fields), ["color", "name"])

    def test_doc_type(self):
        self.assertEqual(self.Car.search.doc_type, "elasticmodels_car")

    def test_duplicate_field_names_not_allowed(self):
        class CarIndex(Index):
            color = StringField()
            # this should trigger the error
            name = StringField()

            class Meta:
                fields = ['name']

        with self.assertRaises(RedeclaredFieldError):
            class Car(models.Model):
                name = models.CharField(max_length=255)

                search = CarIndex()

    def test_mapping(self):
        self.assertEqual(self.Car.search.get_mapping(), {
            'dynamic': "strict",
            'properties': {
                'color': {
                    'type': 'string'
                },
                'name': {
                    'type': 'string'
                }
            }
        })

    def test_put_mapping(self):
        self.assertFalse(self.Car.search.es.indices.exists(self.Car.search.index))
        self.Car.search.put_mapping()
        self.assertTrue(self.Car.search.es.indices.exists(self.Car.search.index))
        self.assertEqual(self.Car.search.es.indices.get_mapping(index=self.Car.search.index, doc_type=self.Car.search.doc_type), {
            'elasticmodels-unit-test-db': {
                'mappings': {
                    'elasticmodels_car': {
                        'properties': {
                            'color': {
                                'type': 'string'
                            },
                            'name': {
                                'type': 'string'
                            }
                        },
                        'dynamic': 'strict'
                    }
                }
            }
        })
        # putting the mapping twice shouldn't be a problem
        self.Car.search.put_mapping()

    def test_delete_mapping(self):
        self.Car.search.put_mapping()
        self.assertEqual(1, len(self.Car.search.es.indices.get_mapping(index=self.Car.search.index, doc_type=self.Car.search.doc_type)))
        self.Car.search.delete_mapping()
        self.assertEqual(0, len(self.Car.search.es.indices.get_mapping(index=self.Car.search.index, doc_type=self.Car.search.doc_type)))

    def test_get_queryset(self):
        # create a dummy index and model to play with
        class CarIndex(Index):
            class Meta:
                fields = ['name']
                date_field = "modified_on"

        class Car(models.Model):
            name = models.CharField(max_length=255)
            modified_on = models.DateTimeField(auto_now=True)

            search = CarIndex()

        date = datetime.datetime(2015, 4, 13, 1, 1, 1, tzinfo=utc)
        queryset = Car.search.get_queryset(start=date)
        self.assertIn('"modified_on" >= 2015-04-13 01:01:01', str(queryset.query))
        queryset = Car.search.get_queryset(end=date)
        self.assertIn('"modified_on" <= 2015-04-13 01:01:01', str(queryset.query))

    def test_update(self):
        car = prepare(self.Car, pk=5)
        # test .update with an single model object
        with patch("elasticmodels.indexes.bulk") as m:
            self.Car.search.update(car)
            self.assertEqual((m.call_args[1]['actions'][0]), {
                '_id': 5,
                '_index': 'elasticmodels-unit-test-db',
                '_source': {
                    'name': car.name,
                    'color': 'blue'
                },
                '_type': 'elasticmodels_car',
                '_op_type': 'index',
            })

        # test .update with an iterable
        with patch("elasticmodels.indexes.bulk") as m:
            self.Car.search.update([car])
            self.assertEqual((m.call_args[1]['actions'])[0], {
                '_id': 5,
                '_index': 'elasticmodels-unit-test-db',
                '_source': {
                    'name': car.name,
                    'color': 'blue'
                },
                '_type': 'elasticmodels_car',
                '_op_type': 'index',
            })

        # test local storage queuing
        local_storage = Mock(bulk_queue={self.Car.search: []})
        with patch("elasticmodels.indexes.local_storage", local_storage):
            self.Car.search.update([car])
            self.assertEqual(list(local_storage.bulk_queue[self.Car.search][0]), [{
                '_index': 'elasticmodels-unit-test-db',
                '_op_type': 'index',
                '_type': 'elasticmodels_car',
                '_id': 5,
                '_source': {
                    'name': car.name,
                    'color': 'blue'
                }
            }])

    def test_delete(self):
        car = prepare(self.Car, pk=5)
        self.Car.search.update(car)
        self.assertEqual(1, len(self.Car.search.query("match", name=car.name).execute().hits))
        self.Car.search.delete(car)
        self.assertEqual(0, len(self.Car.search.query("match", name=car.name).execute().hits))

    def test_prepare(self):
        car = prepare(self.Car, pk=5)
        prepared = self.Car.search.prepare(car)
        self.assertEqual({
            "name": car.name,
            "color": "blue",
        }, prepared)


class IndexRegistryTest(ESTest):
    def test(self):
        r = IndexRegistry()
        # we just mock up indexes and
        A_Model = Mock()
        B_Model = Mock()
        a_index = Mock()
        a2_index = Mock()
        b_index = Mock()

        r.register(A_Model, a_index)
        r.register(A_Model, a2_index)
        r.register(B_Model, b_index)
        self.assertEqual(set(r.get_models()), set([A_Model, B_Model]))
        self.assertEqual(len(r.get_models()), 2)

        self.assertEqual(r.indexes_for_model(A_Model), set([a_index, a2_index]))

    def test_update(self):
        """
        The update method should be called on every index with ignore_signals
        set to False
        """
        r = IndexRegistry()

        class A_Model:
            pass

        index_1 = Mock(ignore_signals=False)
        index_2 = Mock(ignore_signals=False)
        index_3 = Mock(ignore_signals=True)

        r.register(A_Model, index_1)
        r.register(A_Model, index_2)
        r.register(A_Model, index_3)

        instance = A_Model()
        r.update(instance)

        index_1.update.assert_called_with(instance)
        index_2.update.assert_called_with(instance)
        self.assertFalse(index_3.update.called)

    def test_delete(self):
        r = IndexRegistry()

        class A_Model:
            pass

        index_1 = Mock(ignore_signals=False)
        index_2 = Mock(ignore_signals=False)
        index_3 = Mock(ignore_signals=True)

        r.register(A_Model, index_1)
        r.register(A_Model, index_2)
        r.register(A_Model, index_3)

        instance = A_Model()
        r.delete(instance)

        index_1.update.assert_called_with(instance, action="delete")
        index_2.update.assert_called_with(instance, action="delete")
        self.assertFalse(index_3.update.called)


class SuspendedUpdatesTest(ESTest):
    def test(self):
        class CarIndex(Index):
            class Meta:
                fields = ['name']

        class Car(models.Model):
            name = models.CharField(max_length=255)
            search = CarIndex()

        Car.search.put_mapping()

        with suspended_updates():
            car = prepare(Car, pk=1)
            car2 = prepare(Car, pk=2)
            car3 = prepare(Car, pk=3)
            Car.search.update([car])
            Car.search.update(car2)
            Car.search.update(car3)
            # bulk saving shouldn't happen yet
            self.assertEqual([], Car.search.query("match", name=car.name).execute().hits)
            # suspended_updates should handle deletes too
            Car.search.delete(car3)

        # now saving to ES should have happen
        self.assertEqual(1, len(Car.search.query("match", name=car.name).execute().hits))
        self.assertEqual(1, len(Car.search.query("match", name=car2.name).execute().hits))
        # the third car was deleted
        self.assertEqual(0, len(Car.search.query("match", name=car3.name).execute().hits))


class ReceiverTest(ESTest):
    def test_save(self):
        class CarIndex(Index):
            class Meta:
                fields = ['name']

        class Car(models.Model):
            name = models.CharField(max_length=255)
            search = CarIndex()

        with connection.schema_editor() as editor:
            editor.create_model(Car)

        car = prepare(Car)
        # this should add the model to ES
        car.save()
        self.assertEqual(1, len(Car.search.query("match", name=car.name).execute().hits))
        # this should remove the model from ES
        car.delete()
        self.assertEqual(0, len(Car.search.query("match", name=car.name).execute().hits))


class UpdateCommandTest(ESTest):
    def test_parse_date_time(self):
        cmd = UpdateCommand()
        self.assertEqual(
            cmd.parse_date_time("2010-10-10 10:10"),
            timezone.make_aware(datetime.datetime(2010, 10, 10, 10, 10), timezone=timezone.get_current_timezone())
        )
        self.assertEqual(
            cmd.parse_date_time("2010-10-10"),
            timezone.make_aware(datetime.datetime(2010, 10, 10), timezone=timezone.get_current_timezone())
        )
        right_now = now()
        with patch("elasticmodels.management.commands.update_index.timezone.now", Mock(return_value=right_now)):
            self.assertEqual(cmd.parse_date_time("1d"), right_now - datetime.timedelta(days=1))

        with patch("elasticmodels.management.commands.update_index.timezone.now", Mock(return_value=right_now)):
            self.assertEqual(cmd.parse_date_time("1d0h1m"), right_now - datetime.timedelta(days=1, minutes=1))

        with self.assertRaises(ValueError):
            cmd.parse_date_time("asdf")

    def test_handle(self):
        cmd = UpdateCommand()
        model = Dummy()
        index = Mock()
        index.get_queryset = Mock(return_value=Mock(count=lambda: 1))
        index._meta.using = "foo"
        index2 = Mock()
        with patch("elasticmodels.management.commands.update_index.get_models", Mock(return_value=[model])):
            with patch("elasticmodels.management.commands.registry.indexes_for_model", Mock(return_value=[index, index2])):
                cmd.handle(start="2010-10-10", end="2011-11-11", using=["foo"])
                self.assertTrue(index.put_mapping.called)
                self.assertTrue(index.update.called)
                self.assertFalse(index2.put_mapping.called)


class ClearCommandTest(TestCase):
    def test_handle(self):
        cmd = ClearCommand()
        model = Dummy()
        index = Mock()
        index.get_queryset = Mock(return_value=Mock(count=lambda: 1))
        index._meta.using = "foo"
        index2 = Mock()
        with patch("elasticmodels.management.commands.clear_index.get_models", Mock(return_value=[model])):
            with patch("elasticmodels.management.commands.registry.indexes_for_model", Mock(return_value=[index, index2])):
                cmd.handle(using=["foo"], noinput=True)
                # delete mapping on this index should be called, since _meta.using == "foo"
                self.assertTrue(index.delete_mapping.called)
                # delete mapping on this index should not be called, since
                # _meta.using is not "foo"
                self.assertFalse(index2.delete_mapping.called)
                self.assertTrue(cmd.confirmed)

        with patch("elasticmodels.management.commands.clear_index.get_models", Mock(return_value=[model])):
            with patch("elasticmodels.management.commands.registry.indexes_for_model", Mock(return_value=[index, index2])) as m:
                with patch("builtins.input", Mock(return_value="no")):
                    cmd.handle()
                    # make sure indexes_for_models didn't get called
                    self.assertFalse(m.called)
                    self.assertFalse(cmd.confirmed)


class GetModelsTest(TestCase):
    def test_get_models(self):
        model_a = Dummy()
        model_a._meta.app_label = "foo"
        model_b = Dummy()
        model_b._meta.app_label = "bar"
        model_b._meta.model_name = "Bar"
        with patch("elasticmodels.management.commands.registry.get_models", Mock(return_value=[model_a, model_b])):
            # test getting a model by app name
            self.assertEqual(get_models(["foo"]), set([model_a]))
            # test getting a model by app name and model name
            self.assertEqual(get_models(["foo", "bar.Bar"]), set([model_a, model_b]))
            with self.assertRaises(ValueError):
                get_models(['asdf'])
