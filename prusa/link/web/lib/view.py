"""Response generate module."""
from importlib.resources import files
from os.path import join

from jinja2 import Environment, FileSystemLoader, pass_context
from jinja2.runtime import Context
from jinja2_template_info import TemplateInfoExtension
from poorwsgi import redirect

from prusa.connect.printer.const import PrinterType

from .core import app

TEMPL_PATH = (str(files('jinja2_template_info')),
              join(str(files('prusa.link')), 'templates'))


def printer_type(type_):
    """Return name of printer type."""
    # pylint: disable=unused-argument
    if type_ == PrinterType.I3MK3:
        return "Original Prusa i3 MK3"
    if type_ == PrinterType.I3MK3S:
        return "Original Prusa i3 MK3S"
    if type_ == PrinterType.I3MK25:
        return "Original Prusa i3 MK2.5"
    if type_ == PrinterType.I3MK25S:
        return "Original Prusa i3 MK2.5S"
    return "Unknown"


def add_prefix(prefix, uri):
    """Add prefix to uri."""
    if uri[0] != '/':
        raise ValueError("The supplied URI does not start with a slash, "
                         "so it is probably not meant to be prefixed")
    if prefix:
        return f"{prefix}{uri}"
    return uri


@pass_context
def prefix_filter(context: Context, uri):
    """Add prefix to uri."""
    prefix = context.get('uri_prefix')
    return add_prefix(prefix, uri)


def redirect_with_proxy(req, uri):
    """Modifies the redirect uri to include the proxy prefix."""
    redirect(
        add_prefix(
            prefix=req.headers.get("X-Forwarded-Prefix"),
            uri=uri,
        ),
    )


env = Environment(loader=FileSystemLoader(TEMPL_PATH),
                  extensions=[
                      'jinja2.ext.i18n', 'jinja2.ext.do',
                      'jinja2.ext.loopcontrols',
                  ])

env.filters['printer_type'] = printer_type
env.filters['prefixed'] = prefix_filter


def package_to_api(pkg):
    """Convert pkg_resources.DistInfoDistribution to API."""
    return {
        'name': pkg.project_name,
        'version': pkg.version,
        'path': pkg.module_path,
    }


def generate_page(request, template, **kwargs):
    """Return generated ouptut fromjinja template."""
    if app.debug:
        eval_env = env.overlay()
        eval_env.add_extension(TemplateInfoExtension)
        env.globals["template_info"].data = kwargs.copy()
        env.globals['template_info'].template = template
        kwargs['debug'] = True
    else:
        eval_env = env

    kwargs['this_uri'] = request.uri
    kwargs['uri_prefix'] = request.headers.get("X-Forwarded-Prefix")
    tmpl = eval_env.get_template(template)
    return tmpl.render(kwargs)
