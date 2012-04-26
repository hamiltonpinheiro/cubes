# -*- coding: utf-8 -*-
"""Shared SQL utilities"""

import collections

__all__ = (
    "Mapper",
    "PhysicalReference",
    "Join",
    "coalesce_physical",
    "DEFAULT_KEY_FIELD"
)

DEFAULT_KEY_FIELD = "id"

# FIXME: list of required fixes to the AttributeMapper:
#
# * remove need to use dimension where attribute object is passed (dimension
#   should now be part of the attribute)

"""Physical reference to a table column. Note that the table might be an
aliased table name as specified in relevant join."""
PhysicalReference = collections.namedtuple("PhysicalReference",
                                    ["schema", "table", "column"])

"""Table join specification. `master` and `detail` are PhysicalReference
(3-item) tuples"""
Join = collections.namedtuple("Join",
                                    ["master", "detail", "alias"])

def coalesce_physical(ref, default_table=None, schema=None):
    """Coalesce physical reference `ref` which might be:

    * a string in form ``"table.column"``
    * a list in form ``(table, column)``
    * a list in form ``(schema, table, column)``
    * a dictionary with keys: ``schema``, ``table``, ``column`` where
      ``column`` is required, the rest are optional

    Returns tuple (`schema`, `table`, `column`), which is a named tuple
    `PhysicalReference`.

    If no table is specified in reference and `default_table` is not
    ``None``, then `default_table` will be used.
    
    .. note::
        
        The `table` element might be a table alias specified in list of joins.
    
    """

    if isinstance(ref, basestring):
        split = ref.split(".")

        if len(split) > 1:
            dim_name = split[0]
            attr_name = ".".join(split[1:])
            return PhysicalReference(schema, dim_name, attr_name)
        else:
            return PhysicalReference(schema, default_table, ref)
    elif isinstance(ref, dict):
        return PhysicalReference(ref.get("schema") or schema, 
                                 ref.get("table") or default_table,
                                 ref.get("column"))
    else:
        if len(ref) == 2:                         
            return PhysicalReference(schema, ref[0], ref[1])
        elif len(ref) == 3:
            return PhysicalReference(ref[0], ref[1], ref[2])
        else:
            raise Exception("Number of items in table reference should "\
                            "be 2 (table, column) or 3 (schema, table, column)")


class Mapper(object):
    """Mapper is core clas for translating logical model to physical
    database schema.
    """

    def __init__(self, cube, mappings=None, locale=None, schema=None,
                    fact_name=None, dimension_prefix=None, joins=None):
        """Creates a mapper for a cube. The mapper maps logical references to
        physical references (tables and columns), creates required joins,
        resolves table names.
        
        Attributes:
        
        * `cube` - mapped cube
        * `mappings` – dictionary containing mappings
        * `simplify_dimension_references` – references for flat dimensions 
          (with one level and no details) will be just dimension names, no 
          attribute name. Might be useful when using single-table schema, for 
          example, with couple of one-column dimensions.
        * `dimension_table_prefix` – default prefix of dimension tables, if 
          default table name is used in physical reference construction
        * `fact_name` – fact name, if not specified then `cube.name` is used
        * `schema` – default database schema
        * `dimension_prefix` – prefix for dimension tables
        
        Mappings
        --------
        
        Mappings is a dictionary where keys are logical attribute references
        and values are table column references. The keys are mostly in the form:
        
        * ``attribute`` for measures and fact details
        * ``attribute.locale`` for localized fact details
        * ``dimension.attribute`` for dimension attributes
        * ``dimension.attribute.locale`` for localized dimension attributes
        
        The values might be specified as strings in the form ``table.column`` 
        or as two-element arrays: [`table`, `column`].

        .. In the future it might support automatic join detection.
        
        """
        
        super(Mapper, self).__init__()

        if cube == None:
            raise Exception("Cube for mapper should not be None.")

        self.cube = cube
        self.mappings = mappings
        self.locale = locale
        
        self.fact_name = fact_name or self.cube.fact or self.cube.name
        self.schema=schema
        
        self.simplify_dimension_references = True
        self.dimension_table_prefix = dimension_prefix
        
        self.joins = joins
    
        self._collect_attributes()
        self._collect_joins(joins)

    def _collect_attributes(self):
        """Collect all cube attributes and create a dictionary where keys are 
        logical references and values are `cubes.model.Attribute` objects.
        This method should be used after each cube or mappings change.
        """

        self.attributes = {}
        
        for attr in self.cube.measures:
            self.attributes[self.logical(attr)] = attr

        for attr in self.cube.details:
            self.attributes[self.logical(attr)] = attr

        for dim in self.cube.dimensions:
            for level in dim.levels:
                for attr in level.attributes:
                    ref = self.logical(attr)
                    self.attributes[ref] = attr
                    
    def _collect_joins(self, joins):
        """Collects joins and coalesce physical references. `joins` is
        a dictionary with keys: `master`, `detail` reffering to master and detail
        keys. `alias` is used to give alternative name to a table when two
        tables are being joined."""
        
        joins = joins or []

        self.joins = []

        for join in joins:
            master = coalesce_physical(join["master"],self.fact_name)
            detail = coalesce_physical(join["detail"])
            self.joins.append(Join(master, detail, join.get("alias")))

    def logical(self, attribute):
        """Returns logical reference as string for `attribute` in `dimension`. 
        If `dimension` is ``Null`` then fact table is assumed. The logical 
        reference might have following forms:


        * ``dimension.attribute`` - dimension attribute
        * ``attribute`` - fact measure or detail
        
        If `simplify_dimension_references` is ``True`` then references for flat 
        dimensios without details is ``dimension``
        """

        dimension = attribute.dimension

        if dimension:
            if self.simplify_dimension_references and \
                               (dimension.is_flat and not dimension.has_details):
                reference = dimension.name
            else:
                reference = dimension.name + '.' + str(attribute)
        else:
            reference = str(attribute)
            
        return reference

    def split_logical(self, reference):
        """Returns tuple (`dimension`, `attribute`) from `logical_reference` string. Syntax
        of the string is: ``dimensions.attribute``."""
        
        split = reference.split(".")

        if len(split) > 1:
            dim_name = split[0]
            attr_name = ".".join(split[1:])
            return (dim_name, attr_name)
        else:
            return (None, reference)

    def physical(self, attribute, locale = None):
        """Returns physical reference as tuple for `attribute`, which should
        be an instance of :class:`cubes.model.Attribute`. If there is no
        dimension specified in attribute, then fact table is assumed. The
        returned tuple has structure: (`schema`, `table`, `column`).

        The algorithm to find physicl reference is as follows::
        
            IF localization is requested:
                IF is attribute is localizable:
                    IF requested locale is one of attribute locales
                        USE requested locale
                    ELSE
                        USE default attribute locale
                ELSE
                    do not localize

            IF mappings exist:
                GET string for logical reference
                IF locale:
                    append '.' and locale to the logical reference

                IF mapping value exists for localized logical reference
                    USE value as reference

            IF no mappings OR no mapping was found:
                column name is attribute name

                IF locale:
                    append '_' and locale to the column name

                IF dimension specified:
                    # Example: 'date.year' -> 'date.year'
                    table name is dimension name

                    IF there is dimension table prefix
                        use the prefix for table name

                ELSE (if no dimension is specified):
                    # Example: 'date' -> 'fact.date'
                    table name is fact table name
        """
        
        # FIXME: we need schema as well, see Issue #43
        
        reference = None
        dimension = attribute.dimension

        # Fix locale: if attribute is not localized, use none, if it is
        # localized, then use specified if exists otherwise use default
        # locale of the attribute (first one specified in the list)

        try:
            if attribute.locales:
                locale = locale if locale in attribute.locales \
                                    else attribute.locales[0]
            else:
                locale = None
        except:
            locale = None

        # Try to get mapping if exists
        if self.cube.mappings:
            logical = self.logical(attribute)
            # Append locale to the logical reference

            if locale:
                logical += "." + locale

            # TODO: should default to non-localized reference if no mapping 
            # was found?
            mapped_ref = self.cube.mappings.get(logical)

            if mapped_ref:
                reference = coalesce_physical(mapped_ref, self.fact_name, self.schema)

        # No mappings exist or no mapping was found - we are going to create
        # default physical reference
        if not reference:
            column_name = str(attribute)

            if locale:
                column_name += "_" + locale
            
            if dimension and not (self.simplify_dimension_references \
                                   and (dimension.is_flat 
                                        and not dimension.has_details)):
                table_name = str(dimension)
                if self.dimension_table_prefix:
                    table_name = self.dimension_table_prefix + table_name

            else:
                table_name = self.fact_name

            reference = PhysicalReference(self.schema, table_name, column_name)

        return reference

    def table_map(self):
        """Return list of references to all tables. Keys are aliased
        tables: (`schema`, `aliased_table_name`) and values are
        real tables: (`schema`, `table_name`). Included is the fact table
        and all tables mentioned in joins.
        
        To get list of all physical tables where aliased tablesare included
        only once::
        
            finder = JoinFinder(cube, joins, fact_name)
            tables = set(finder.table_map().keys())
        """
        
        tables = {
            (self.schema, self.fact_name): (self.schema, self.fact_name)
        }
        
        for join in self.joins:
            if not join.detail.table or join.detail.table == self.fact_name:
                raise ValueError("Detail table name should be present and should not be a fact table.")

            ref = (join.master.schema, join.master.table)
            tables[ref] = ref

            ref = (join.detail.schema, join.alias or join.detail.table)
            tables[ref] = (join.detail.schema, join.detail.table)

        return tables
        
    def relevant_joins(self, attributes):
        """Get relevant joins to the attributes - list of joins that 
        are required to be able to acces specified attributes. `attributes`
        is a list of three element tuples: (`schema`, `table`, `attribute`).
        """
        
        # Attribute: (schema, table, column)
        # Join: ((schema, table, column), (schema, table, column), alias)

        tables_to_join = {(ref[0], ref[1]) for ref in attributes}
        joined_tables = set()
        
        joins = []
        
        while tables_to_join:
            table = tables_to_join.pop()
            # print "==> JOINING TABLE: %s" % (table, )
            
            for join in self.joins:
                # print "--- testing join: %s" % (join, )
                master = (join.master.schema, join.master.table)
                detail = (join.detail.schema, join.alias or join.detail.table)

                if table == detail:
                    # print "--> detail matches"
                    joins.append(join)

                    if master not in joined_tables:
                        # print "--- adding master to be joined"
                        tables_to_join.add(master)

                    # print "--+ joined: %s" % (detail, )
                    joined_tables.add(detail)
                    break
        
        # FIXME: is join order important? if yes, we should sort them here
        
        return joins
