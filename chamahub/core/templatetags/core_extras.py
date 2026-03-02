# core/templatetags/core_extras.py
from django import template

register = template.Library()

@register.filter
def model_name(obj):
    """
    Returns the class name of an object
    Usage: {{ object|model_name }}
    """
    if obj is None:
        return ''
    return obj.__class__.__name__

@register.filter
def get_type(value):
    """
    Returns the type of a value as a string
    """
    return type(value).__name__

@register.filter
def class_name(value):
    """
    Alternative name for model_name filter
    """
    return value.__class__.__name__