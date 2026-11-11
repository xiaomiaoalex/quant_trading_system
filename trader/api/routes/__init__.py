"""HTTP route modules.

The package intentionally does not import route modules eagerly. The legacy
process imported every router (including the retired execution surfaces) as a
side effect of importing any single module, which made it impossible to mount
the Control API without also importing the old stack. Import the module you
need explicitly instead.
"""
