from django import forms
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.conf import settings

class SearchForm(forms.Form):
    """
    This is the base form class for search forms. It comes with a a nice q
    field that automatically searches the text field on the Model's index.

    Subclasses need to implement queryset() and search()
    """
    q = forms.CharField(required=False, label="", widget=forms.widgets.TextInput(attrs={"placeholder": "Search"}))
    sort_by_relevance = True

    def __init__(self, *args, **kwargs):
        super(SearchForm, self).__init__(*args, **kwargs)
        self.cleaned_data = {}
        self.is_valid()

    def in_search_mode(self):
        """
        Returns true if any of the form fields were filled out
        """
        if not hasattr(self, "_in_search_mode"):
            self._in_search_mode = any(self.cleaned_data.values())
        return self._in_search_mode

    def search(self):
        """
        This should return a Haystack queryset based on the values of
        self.cleaned_data. You do not need to take into account the q field,
        since that is handled automatically
        """
        raise NotImplementedError("You must implement the search method!")

    def queryset(self):
        """
        This should return the list of objects when a search is not performed.
        It also serves to filter out search results which are not part of this
        set
        """
        raise NotImplementedError("You must implement the queryset method!")

    def results(self, page):
        """
        This returns a Paginator page object of the results
        """
        if self.in_search_mode():
            objects = self.search()
            # reduce the results based on the q field
            if self.cleaned_data.get("q"):
                objects = objects.query(content__match=self.cleaned_data['q'])
        else:
            objects = self.queryset()

        paginator = Paginator(objects, settings.ITEMS_PER_PAGE)
        try:
            a_page = paginator.page(page)
        except PageNotAnInteger:
            a_page = paginator.page(1)
        except EmptyPage:
            a_page = paginator.page(paginator.num_pages)

        if self.in_search_mode():
            # if we are doing a search, we need to swap out the paginator's
            # object_list with the actual preparation objects (since those aren't
            # stored in the search index). Build up a dict that has the object
            # pk as a key, and the order of the object as the value, so we can
            # sort the objects by it
            pk_lookup = dict((int(search_result.pk), i) for i, search_result in enumerate(a_page.object_list))
            a_page.object_list = list(self.queryset().filter(pk__in=pk_lookup.keys()))
            # we need to sort the objects based on the order they were returned
            # by elasticsearch
            if self.sort_by_relevance:
                a_page.object_list.sort(key=lambda item: pk_lookup[item.pk])

        return a_page
