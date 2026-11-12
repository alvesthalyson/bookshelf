from django.contrib import admin
from django.urls import path, include
from library import pwa_views, views as library_views  # <-- Importa o views da sua pasta library

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('library.urls')), # Mantenha as rotas da sua API
    path('', library_views.home, name='home'), # <-- Usa a view correta aqui
    # installable app: manifest, service worker (must be at the root to cover the whole site) and icons
    path('manifest.webmanifest', pwa_views.manifest, name='manifest'),
    path('sw.js', pwa_views.service_worker, name='service-worker'),
    path('icons/<str:name>', pwa_views.icon, name='icon'),
]