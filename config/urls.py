from django.contrib import admin
from django.urls import path, re_path, include
from django.views.generic import RedirectView
from library import pwa_views, views as library_views  # <-- Importa o views da sua pasta library

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('library.urls')), # Mantenha as rotas da sua API
    path('', library_views.home, name='home'), # <-- Usa a view correta aqui
    # short and upper-case addresses lead to the API (Django treats /API/ and /api/ as different addresses)
    path('books/', RedirectView.as_view(url='/api/books/', query_string=True)),
    path('authors/', RedirectView.as_view(url='/api/authors/', query_string=True)),
    re_path(r'^API/(?P<rest>.*)$', RedirectView.as_view(url='/api/%(rest)s', query_string=True)),
    # installable app: manifest, service worker (must be at the root to cover the whole site) and icons
    path('manifest.webmanifest', pwa_views.manifest, name='manifest'),
    path('sw.js', pwa_views.service_worker, name='service-worker'),
    path('icons/<str:name>', pwa_views.icon, name='icon'),
]