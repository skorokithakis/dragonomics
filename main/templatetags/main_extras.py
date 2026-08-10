"""Template filters for the audience pages."""

from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """Dictionary lookup by key, e.g. ``{{ payload.requested|get_item:name }}``."""
    try:
        return mapping[key]
    except (KeyError, TypeError):
        return ""
