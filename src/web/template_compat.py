from __future__ import annotations

from typing import Any

from fastapi.templating import Jinja2Templates


class CompatJinja2Templates(Jinja2Templates):
    """Compatibility wrapper for Starlette/FastAPI TemplateResponse.

    The project still uses the pre-Starlette-1.0 calling convention in many
    routes:

        templates.TemplateResponse("page.html", {"request": request, ...})

    Starlette 1.0 changed the signature to:

        templates.TemplateResponse(request, "page.html", context)

    Without this shim, the old call passes the context dict as the template
    name and Jinja raises ``TypeError: unhashable type: 'dict'``.
    """

    def TemplateResponse(self, *args: Any, **kwargs: Any):  # noqa: N802 - upstream API name
        if args and isinstance(args[0], str):
            name = args[0]
            context = args[1] if len(args) > 1 else kwargs.pop("context", None)
            if context is None:
                context = {}
            request = kwargs.pop("request", None) or context.get("request")
            if request is None:
                raise ValueError(
                    "TemplateResponse old-style call requires context['request']"
                )
            return super().TemplateResponse(request, name, context, *args[2:], **kwargs)

        return super().TemplateResponse(*args, **kwargs)
