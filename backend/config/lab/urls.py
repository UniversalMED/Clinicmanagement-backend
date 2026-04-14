from django.urls import path
from .views import (
    LabTestListView,
    LabTestDetailView,
    TestOrderListView,
    TestOrderDetailView,
    TestResultListView,
    TestResultDetailView,
)

urlpatterns = [
    path('tests/', LabTestListView.as_view(), name='lab-test-list'),
    path('tests/<uuid:test_id>/', LabTestDetailView.as_view(), name='lab-test-detail'),
    path('orders/', TestOrderListView.as_view(), name='test-order-list'),
    path('orders/<uuid:order_id>/', TestOrderDetailView.as_view(), name='test-order-detail'),
    path('results/', TestResultListView.as_view(), name='test-result-list'),
    path('results/<uuid:result_id>/', TestResultDetailView.as_view(), name='test-result-detail'),
]
