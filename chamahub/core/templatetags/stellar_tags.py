# core/templatetags/stellar_tags.py
"""
Template tags for rendering Stellar blockchain badges and explorer links.

Usage in templates:
    {% load stellar_tags %}
    {% stellar_badge contribution %}
    {% stellar_explorer_link contribution.stellar_tx_hash %}
    {% stellar_explorer_link contribution.stellar_tx_hash text="View on Stellar" %}
"""

from django import template
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def stellar_badge(obj):
    """
    Render a blockchain verification badge for any StellarMixin model instance.

    Shows a green "Blockchain Verified" badge (linked to Stellar Expert) if the
    object has a recorded tx hash, or a grey "Pending" badge if not.

    Usage:
        {% stellar_badge contribution %}
        {% stellar_badge loan %}
        {% stellar_badge repayment %}
        {% stellar_badge withdrawal %}
    """
    if not obj or not hasattr(obj, 'is_on_stellar'):
        return ''

    if obj.is_on_stellar() and obj.stellar_tx_hash:
        # ✅ format_html escapes all interpolated values — safe against XSS
        # even if stellar_tx_hash somehow contains malicious content
        url = obj.get_stellar_url()
        return format_html(
            '''<a href="{url}" target="_blank" rel="noopener noreferrer"
                  class="badge bg-success text-decoration-none"
                  title="Verified on Stellar Blockchain — click to view">
                   <i class="fas fa-link me-1"></i>Blockchain Verified
               </a>''',
            url=url,
        )

    return mark_safe(
        '''<span class="badge bg-secondary"
                title="Not yet recorded on Stellar blockchain">
               <i class="fas fa-clock me-1"></i>Pending Verification
           </span>'''
        # mark_safe is fine here — no user data is interpolated at all
    )


@register.simple_tag
def stellar_explorer_link(tx_hash, text=None):
    """
    Render a clickable link to the Stellar Expert explorer for a given tx hash.

    Args:
        tx_hash (str): The Stellar transaction hash
        text (str, optional): Link label. Defaults to first 10 chars of hash + '...'

    Usage:
        {% stellar_explorer_link contribution.stellar_tx_hash %}
        {% stellar_explorer_link contribution.stellar_tx_hash text="View on Stellar" %}
    """
    if not tx_hash:
        return ''

    network = 'testnet'  # Change to 'public' when going live on mainnet
    url = f"https://stellar.expert/explorer/{network}/tx/{escape(tx_hash)}"

    # Truncate long hashes for display — escape before rendering
    display_text = text if text else f"{tx_hash[:10]}..."

    # ✅ format_html escapes both url and display_text — safe against XSS
    return format_html(
        '<a href="{url}" target="_blank" rel="noopener noreferrer" '
        'class="font-monospace small">{text}</a>',
        url=url,
        text=display_text,
    )


@register.simple_tag
def stellar_recorded_at(obj):
    """
    Render a small human-readable timestamp for when a transaction was
    recorded on Stellar. Returns empty string if not yet recorded.

    Usage:
        {% stellar_recorded_at contribution %}
    """
    if not obj or not hasattr(obj, 'stellar_recorded_at'):
        return ''

    if obj.stellar_recorded_at:
        return format_html(
            '<span class="text-muted small" title="{full}">'
            '<i class="fas fa-cube me-1"></i>{date}'
            '</span>',
            full=obj.stellar_recorded_at.strftime('%Y-%m-%d %H:%M:%S UTC'),
            date=obj.stellar_recorded_at.strftime('%d %b %Y'),
        )

    return ''


@register.filter
def short_hash(tx_hash, length=10):
    """
    Truncate a Stellar transaction hash for compact display.

    Usage:
        {{ contribution.stellar_tx_hash|short_hash }}
        {{ contribution.stellar_tx_hash|short_hash:16 }}
    """
    if not tx_hash:
        return ''
    try:
        length = int(length)
    except (ValueError, TypeError):
        length = 10
    return f"{tx_hash[:length]}..." if len(tx_hash) > length else tx_hash

# In core/templatetags/stellar_tags.py
# Add this filter anywhere alongside your existing ones

@register.filter(name='model_name')
def model_name(obj):
    """
    Returns the model class name as a string.
    Usage: {{ item|model_name }}  →  'Contribution', 'Repayment', 'Withdrawal'
    """
    return obj.__class__.__name__