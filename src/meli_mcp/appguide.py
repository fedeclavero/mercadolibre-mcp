"""Exact values to type in the Mercado Libre DevCenter form, taken from the official pages
"Crea una aplicación en Mercado Libre" (updated 06/08/2026) and "Permisos funcionales" (updated 04/03/2026)."""
from __future__ import annotations

from typing import Any

from .authflow import DEFAULT_REDIRECT, DEVCENTER

PERMISSION_GROUPS = (
    "Usuarios (viene activo por defecto)",
    "Publicación y sincronización",
    "Comunicación pre y postventa",
    "Publicidad",
    "Métricas del negocio",
    "Ventas y envíos",
    "Promociones, cupones y descuentos",
    "Facturación",
)
FALLBACK_REDIRECTS = (
    "https://127.0.0.1:8765/callback",
    "https://www.mercadolibre.com.ar/",
)


def app_checklist(country: str = "ar", redirect: str = DEFAULT_REDIRECT) -> dict[str, Any]:
    country = country.lower()
    return {
        "devcenter_url": DEVCENTER[country],
        "antes_de_empezar": [
            "Entrá con la cuenta TITULAR (no con un colaborador/operador): esa cuenta será la dueña de la app.",
            "En Argentina, México, Brasil y Chile el DevCenter solo deja crear apps cuando los datos del titular de la cuenta están cargados y validados; si pide validar datos, completalos primero.",
            "Mercado Libre exige una app por unidad (Mercado Libre y Mercado Pago por separado, desde el 30/08/2026): creá esta app en el DevCenter de Mercado Libre y no tildes permisos de Mercado Pago.",
        ],
        "pasos": [
            f"1. Abrí {DEVCENTER[country]} e iniciá sesión.",
            "2. Tocá \"Crear nueva aplicación\".",
            "3. Nombre: uno único, por ejemplo \"Mi tienda MCP\" (si dice que ya existe, agregale un número).",
            "4. Descripción (hasta 150 caracteres): \"Gestión de mi cuenta con un asistente de IA\".",
            "5. Logo: subí cualquier imagen que pida el formulario (puede ser el logo de tu tienda).",
            f"6. URI de redirección (obligatoriamente HTTPS): {redirect}",
            "7. Marcá \"Use PKCE\" (recomendado).",
            "8. NO marques \"Device Grant\".",
            "9. Scopes y permisos funcionales: en CADA grupo elegí \"Lectura y escritura\": " + "; ".join(PERMISSION_GROUPS) + ". Si el formulario ofrece \"acceso offline\" / offline_access, marcalo (permite renovar el acceso sin volver a iniciar sesión).",
            "10. Tópicos: no marques ninguno (este servidor no recibe notificaciones). Si el formulario igual exige una URL de notificaciones, poné la misma URI de redirección.",
            "11. Guardá. La app aparece en \"Mis aplicaciones\": copiá el APP ID (Client ID) y el Client Secret (Secret Key; mostralo con el ojo/\"Mostrar\").",
        ],
        "si_el_formulario_rechaza_la_uri": [f"Probá con {u}" for u in FALLBACK_REDIRECTS] + ["O usá una URL HTTPS de un dominio tuyo. Después pasá esa misma URI exacta a `mercadolibre-mcp setup --redirect-uri`."],
        "valores_a_copiar": ["APP ID (Client ID)", "Client Secret (Secret Key)"],
        "redirect_uri_sugerida": redirect,
        "nota_localhost": "La documentación oficial exige HTTPS en la URI de redirección pero no dice si acepta localhost; si el formulario la acepta, `mercadolibre-mcp authorize` captura el permiso solo (el navegador mostrará una advertencia de certificado: es normal en localhost, tocá Avanzado y Continuar).",
    }
