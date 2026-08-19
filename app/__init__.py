# Application package root.
#
# This file is what makes `app` a regular package rather than an
# implicit namespace package. Nuitka needs that: `--module app
# --include-package=app` walks a real package to find the submodules it
# must fold into the single .so, and a namespace package has no
# __init__ for it to start from.
#
# Deliberately empty of code — anything executed here would run before
# app.core.config had a chance to load settings.
