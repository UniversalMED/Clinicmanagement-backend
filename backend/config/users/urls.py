from django.urls import path
from .views import CurrentUserView, UserListView, AssignRoleView, UpdateUserView

urlpatterns = [
    path('me/', CurrentUserView.as_view(), name='user-me'),
    path('', UserListView.as_view(), name='user-list'),
    path('<uuid:user_id>/role/', AssignRoleView.as_view(), name='user-assign-role'),
    path('<uuid:user_id>/', UpdateUserView.as_view(), name='user-update'),
]
