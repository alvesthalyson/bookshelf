from django.contrib import admin
from django.urls import path, include
from library import views as library_views  # <-- Importa o views da sua pasta library

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('library.urls')), # Mantenha as rotas da sua API
    path('', library_views.home, name='home'), # <-- Usa a view correta aqui
]