def dashboard_mode_context(request):
    mode = 'member'
    can_switch_profile = False

    if request.user.is_authenticated:
        try:
            profile = request.user.chama_profile
            can_switch_profile = profile.is_treasurer()
        except Exception:
            can_switch_profile = False

        if can_switch_profile:
            mode = request.session.get('dashboard_mode', 'treasurer')
            if mode not in {'member', 'treasurer'}:
                mode = 'treasurer'
        else:
            mode = 'member'

    return {
        'can_switch_profile': can_switch_profile,
        'active_dashboard_mode': mode,
        'active_dashboard_label': 'Treasurer' if mode == 'treasurer' else 'Member',
    }
