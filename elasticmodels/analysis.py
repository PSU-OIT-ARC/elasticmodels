import json
import difflib
from collections import defaultdict

from six import string_types, text_type
from elasticsearch_dsl.connections import connections
from django.conf import settings
from .indexes import registry


def compare_dicts(d1, d2):
    """
    Returns a diff string of the two dicts.
    """
    a = json.dumps(d1, indent=4, sort_keys=True)
    b = json.dumps(d2, indent=4, sort_keys=True)
    # stolen from cpython
    # https://github.com/python/cpython/blob/01fd68752e2d2d0a5f90ae8944ca35df0a5ddeaa/Lib/unittest/case.py#L1091
    diff = ('\n' + '\n'.join(difflib.ndiff(
                   a.splitlines(),
                   b.splitlines())))
    return diff


def stringer(x):
    """
    Takes an object and makes it stringy
    >>> print(stringer({'a': 1, 2: 3, 'b': [1, 'c', 2.5]}))
    {'b': ['1', 'c', '2.5'], 'a': '1', '2': '3'}
    """
    if isinstance(x, string_types):
        return x
    if isinstance(x, (list, tuple)):
        return [stringer(y) for y in x]
    if isinstance(x, dict):
        return dict((stringer(a), stringer(b)) for a, b in x.items())
    return text_type(x)


def diff_analysis(using):
    """
    Returns a diff string comparing the analysis defined in ES, with
    the analysis defined in Python land for the connection `using`
    """
    python_analysis = collect_analysis(using)
    es_analysis = existing_analysis(using)
    return compare_dicts(es_analysis, python_analysis)


def collect_analysis(using):
    """
    generate the analysis settings from Python land
    """
    python_analysis = defaultdict(dict)
    for index in registry.indexes_for_connection(using):
        python_analysis.update(index._doc_type.mapping._collect_analysis())

    return stringer(python_analysis)


def existing_analysis(using):
    """
    Get the existing analysis for the `using` Elasticsearch connection
    """
    es = connections.get_connection(using)
    index_name = settings.ELASTICSEARCH_CONNECTIONS[using]['index_name']
    if es.indices.exists(index=index_name):
        return stringer(es.indices.get_settings(index=index_name)[index_name]['settings']['index'].get('analysis', {}))
    return {}


def is_analysis_compatible(using):
    """
    Returns True if the analysis defined in Python land and ES for the connection `using` are compatible
    """
    python_analysis = collect_analysis(using)
    es_analysis = existing_analysis(using)

    # we want to ensure everything defined in Python land is exactly matched in ES land
    for section in python_analysis:
        # there is an analysis section (analysis, tokenizers, filters, etc) defined in Python that isn't in ES
        if section not in es_analysis:
            return False

        # for this section of analysis (analysis, tokenizer, filter, etc), get
        # all the items defined in that section, and make sure they exist, and
        # are equal in Python land
        subdict_python = python_analysis[section]
        subdict_es = es_analysis[section]
        for name in subdict_python:
            # this analyzer, filter, etc isn't defined in ES
            if name not in subdict_es:
                return False
            # this analyzer, filter etc doesn't match what is in ES
            if subdict_python[name] != subdict_es[name]:
                return False

    return True


def combined_analysis(using):
    """
    Combine the analysis in ES with the analysis defined in Python. The one in
    Python takes precedence
    """
    python_analysis = collect_analysis(using)
    es_analysis = existing_analysis(using)

    # we want to ensure everything defined in Python land is added, or
    # overrides the things defined in ES
    for section in python_analysis:
        if section not in es_analysis:
            es_analysis[section] = python_analysis[section]

        subdict_python = python_analysis[section]
        subdict_es = es_analysis[section]
        for name in subdict_python:
            subdict_es[name] = subdict_python[name]

    return es_analysis
