"""Alternative context-packing strategies.

Each strategy in this subpackage implements ``ContextStrategy`` and is
registered with the factory via ``runtime.context.factory.register_strategy``
during lazy bootstrap. Add a new strategy by:

1. Subclass / implement ``ContextStrategy`` in a new module here.
2. Add a registration line in ``runtime/context/factory.py:_ensure_builtins_registered``.
3. Document defaults in ``config.yml`` under ``runtime.context.params.<name>``.
"""
