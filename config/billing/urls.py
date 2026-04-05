from django.urls import path
from .views import (
    InvoiceListView,
    InvoiceDetailView,
    InvoiceLineItemsView,
    InvoiceLineItemDetailView,
    InvoiceFinalizeView,
    InvoiceVoidView,
    InvoicePayView,
    InvoicePaymentListView,
    ChapaWebhookView,
)

urlpatterns = [
    path('invoices/',                                         InvoiceListView.as_view()),
    path('invoices/<uuid:invoice_id>/',                       InvoiceDetailView.as_view()),
    path('invoices/<uuid:invoice_id>/items/',                 InvoiceLineItemsView.as_view()),
    path('invoices/<uuid:invoice_id>/items/<uuid:item_id>/',  InvoiceLineItemDetailView.as_view()),
    path('invoices/<uuid:invoice_id>/finalize/',              InvoiceFinalizeView.as_view()),
    path('invoices/<uuid:invoice_id>/void/',                  InvoiceVoidView.as_view()),
    path('invoices/<uuid:invoice_id>/pay/',                   InvoicePayView.as_view()),
    path('invoices/<uuid:invoice_id>/payments/',              InvoicePaymentListView.as_view()),
    path('webhook/chapa/',                                    ChapaWebhookView.as_view()),
]
