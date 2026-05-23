from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls), # Возвращаем админку
    path('', include('drilling.urls')),
]