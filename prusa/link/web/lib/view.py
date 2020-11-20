"""Response generate module."""
from os.path import join, pardir, dirname, abspath

from jinja2 import Environment, FileSystemLoader
from jinja2_template_info import TemplateInfoExtension, __file__ as info_file

from . core import app, TEMPL_DIR

PKG_DIR = abspath(join(dirname(info_file)))
TEMPLATE_INFO_DIR = abspath(
        join(PKG_DIR, pardir, pardir, pardir,
             'share', 'jinja2_template_info', 'templates'))


TEMPL_PATH = (TEMPLATE_INFO_DIR, TEMPL_DIR)
print(TEMPL_PATH)


def generate_page(request, template, **kwargs):
    """Return generated ouptut fromjinja template."""

    env = Environment(loader=FileSystemLoader(TEMPL_PATH),
                      extensions=['jinja2.ext.i18n', 'jinja2.ext.do',
                                  'jinja2.ext.loopcontrols'])
    if app.debug:
        env.add_extension(TemplateInfoExtension)
        env.globals['template_info'].data = kwargs.copy()
        env.globals['template_info'].template = template

    kwargs['this_uri'] = request.uri
    tmpl = env.get_template(template)
    return tmpl.render(kwargs)
