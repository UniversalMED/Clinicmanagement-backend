from django.apps import apps
from django.test.runner import DiscoverRunner


class UnmanagedModelTestRunner(DiscoverRunner):
    """
    All production models use managed=False because their tables are owned by
    Supabase. This runner temporarily flips every unmanaged model to managed=True
    before Django creates the test database, then restores the original state.
    """

    def setup_databases(self, **kwargs):
        self._unmanaged = []
        for model in apps.get_models():
            if not model._meta.managed:
                model._meta.managed = True
                self._unmanaged.append(model)
        return super().setup_databases(**kwargs)

    def teardown_databases(self, old_config, **kwargs):
        super().teardown_databases(old_config, **kwargs)
        for model in self._unmanaged:
            model._meta.managed = False
