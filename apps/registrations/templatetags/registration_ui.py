from django import template
from apps.registrations.instructions import PICKUP_INSTRUCTIONS

register = template.Library()


@register.simple_tag
def pickup_instructions():
    return PICKUP_INSTRUCTIONS
