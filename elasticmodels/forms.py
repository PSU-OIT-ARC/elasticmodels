from elasticsearch_dsl import Search
from django import forms
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.conf import settings

class BaseSearchForm(forms.Form):
    """
    This is the base form class for search forms. It comes with a a nice q
    field that automatically does a multi_match search

    Subclasses need to implement queryset() and search()
    """
    q = forms.CharField(required=False, label="", widget=forms.widgets.TextInput(attrs={"placeholder": "Search"}))

    def __init__(self, data, *args, index, **kwargs):
        self.index = index
        super().__init__(data, **kwargs)
        # since no fields were filled out, then reset data to the empty dict,
        # and unbind the form
        if not any(self.add_prefix(field) in self.data for field in self.fields):
            self.data = {}
            self.is_bound = False

        # populate cleaned_data with the initial values for the fields, so we
        # can filter by them if necessary in search() and get_queryset()
        if not self.is_bound:
            for name, field in self.fields.items():
                value = self.initial.get(name, field.initial)
                self.cleaned_data[name] = value

    @property
    def cleaned_data(self):
        """
        When cleaned_data is initially accessed, we want to ensure the form
        gets validated which has the side effect of setting cleaned_data to
        something.
        """
        if not hasattr(self, "_cleaned_data"):
            self._cleaned_data = {}
            self.is_valid()
        return self._cleaned_data

    @cleaned_data.setter
    def cleaned_data(self, value):
        self._cleaned_data = value

    def in_search_mode(self):
        """
        This should return True if a search should be performed. The
        default implementation will return True if any of the fields appeared
        in self.data

        Subclasses might want to override this so certain fields on the form
        don't trigger a search (for example, fields that should just filter
        down the queryset)
        """
        return self.is_bound

    def get_fields(self):
        return list(self.index.objects._doc_type._fields().keys())

    def search(self):
        """
        This should return an elasticsearch-DSL Search instance, list or
        queryset based on the values in self.cleaned_data.
        """
        results = self.index.objects.all()
        # reduce the results based on the q field
        if self.cleaned_data.get("q"):
            results = results.query(
                "multi_match",
                query=self.cleaned_data['q'],
                fields=self.get_fields(),
                # this prevents ES from erroring out when a string is used on a
                # number field (for example)
                lenient=True
            )

        return results

    def get_queryset(self):
        """
        This should return the queryset of objects that are allowed to be
        included in the results. You are free to use self.cleaned_data
        to filter the queryset based on the values in the form.

        When the form is not in_search_mode, this queryset will be returned in
        results()
        """
        return self.index.objects.get_queryset()

    def is_valid_query(self, search_instance):
        validate = self.index.objects.es.indices.validate_query(
            index=self.index._doc_type.index,
            doc_type=self.index._doc_type.mapping.doc_type,
            body={'query': search_instance.to_dict()['query']},
            explain=True,
        )

        return validate['valid']

    def results(self):
        """
        This either returns self.get_queryset(), self.search(), or
        self.search() wrapped up in a Pageable.
        """
        if not self.in_search_mode():
            return self.get_queryset()

        # we are doing a search
        objects = self.search()

        # if objects isn't a Search object, just return it, since it's
        # (hopefully) just a list or queryset.
        if not isinstance(objects, Search):
            return objects

        if not self.is_valid_query(objects):
            self.add_error(None, forms.ValidationError("Invalid Query", code="invalid-query"))
            return []

        # convert the search results to something that can be iterated over, and paged
        return Pageable(objects, self.get_queryset())


class SearchForm(BaseSearchForm):
    """
    Paginates the results of the BaseSearchForm.results(), and pre-filters the
    Search object based on the results of self.get_queryset(), which makes
    pagination reliable
    """
    def search(self):
        # this is horribly inefficent, but the only way we can guarantee all the
        # results we get back are in the queryset.
        return super().search().filter("ids", values=[int(val) for val in self.get_queryset().values_list('pk', flat=True)])

    def results(self, page, items_per_page=getattr(settings, "ITEMS_PER_PAGE", 100)):
        objects = super().results()

        paginator = Paginator(objects, items_per_page)
        try:
            a_page = paginator.page(page)
        except PageNotAnInteger:
            a_page = paginator.page(1)
        except EmptyPage:
            a_page = paginator.page(paginator.num_pages)

        return a_page


class Pageable:
    """
    wrap up the elasticsearch-dsl Search instance in something that we
    can use in a Paginator, and iterate over, which returns model
    objects

    USING PAGINATION WITH THIS CLASS IS NOT RELIABLE, *unless* you prefiltered
    self.search with the PKs in self.queryset. If you didn't, then count()
    could include items that aren't in the queryset anymore (for example, if
    you deleted things from the database, but not from ES).
    """
    def __init__(self, search, queryset):
        self.search = search
        self.queryset = queryset

    def count(self):
        return self.search.count()

    def __iter__(self):
        return iter(self[0:self.count()])

    def __getitem__(self, key):
        results = list(self.search[key].execute())
        pk_to_model = dict((str(row.pk), row) for row in self.queryset.filter(pk__in=[result.meta.id for result in results]))
        # we need to return the model objects in the order they were retrieved
        # from ES
        to_return = []
        for result in results:
            if result.meta.id in pk_to_model:
                to_return.append(pk_to_model[result.meta.id])
        return to_return
