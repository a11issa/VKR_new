# drilling/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('project/<int:pk>/', views.project_detail, name='project_detail'),
    path('project/<int:proj_id>/del_int/<int:pk>/', views.delete_interval, name='delete_interval'),
    path('project/<int:pk>/pdf/', views.export_pdf, name='export_pdf'),
    path('admin-panel/', views.admin_panel, name='admin_panel'),
]