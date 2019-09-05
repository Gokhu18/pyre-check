# Copyright (c) 2016-present, Facebook, Inc.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree

# pyre-strict

from typing import IO, Any, Dict, List, NamedTuple, Optional, Tuple, Union

import libcst as cst


def _get_attribute_as_string(attribute: Union[cst.Attribute, cst.Name]) -> str:
    names = []
    while isinstance(attribute, cst.Attribute):
        # pyre-fixme[16]: `BaseExpression` has no attribute `value`.
        if isinstance(attribute.value.value, cst.Attribute):
            value = _get_attribute_as_string(
                # pyre-fixme[6]: Expected `Union[Attribute, Name]` for 1st param but
                #  got `BaseExpression`.
                cst.ensure_type(attribute.value, cst.Attribute).value
            )
        else:
            value = _get_name_as_string(attribute.value.value)
        names.append(value)
        attribute = attribute.attr
    names.append(_get_name_as_string(attribute.value))
    return ".".join(names)


def _get_name_as_string(node: Union[cst.CSTNode, str]) -> str:
    if isinstance(node, cst.Name):
        return node.value
    else:
        # pyre-fixme[7]: Expected `str` but got `Union[CSTNode, str]`.
        return node


def _is_in_list(list: List[cst.CSTNode], target: cst.CSTNode) -> bool:
    for item in list:
        if item.deep_equals(target):
            return True
    return False


class FunctionAnnotation(NamedTuple):
    parameters: cst.Parameters
    returns: Optional[cst.Annotation]


class ImportStatement(NamedTuple):
    module: Union[cst.Name, cst.Attribute]
    names: List[cst.CSTNode]


class TypeCollector(cst.CSTVisitor):
    def __init__(self) -> None:
        # Qualifier for storing the canonical name of the current function.
        self.qualifier: List[str] = []
        # Store the annotations.
        self.function_annotations: Dict[str, FunctionAnnotation] = {}
        self.attribute_annotations: Dict[str, cst.Annotation] = {}
        self.imports: Dict[str, ImportStatement] = {}

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self.qualifier.append(node.name.value)

    def leave_ClassDef(self, node: cst.ClassDef) -> None:
        self.qualifier.pop()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self.qualifier.append(node.name.value)
        if node.returns is not None:
            # pyre-fixme[6]: Expected `CSTNode` for 1st param but got
            #  `Optional[Annotation]`.
            return_annotation = self._create_import_from_annotation(node.returns)
            self.function_annotations[".".join(self.qualifier)] = FunctionAnnotation(
                parameters=node.params,
                # pyre-fixme[6]: Expected `Optional[Annotation]` for 2nd param but got
                #  `CSTNode`.
                returns=return_annotation,
            )
        # pyi files don't support inner functions, return False to stop the traversal.
        return False

    def leave_FunctionDef(self, node: cst.FunctionDef) -> None:
        self.qualifier.pop()

    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
        # pyre-fixme[16]: `BaseExpression` has no attribute `value`.
        self.qualifier.append(node.target.value)
        annotation_value = self._create_import_from_annotation(node.annotation)
        # pyre-fixme[6]: Expected `Annotation` for 2nd param but got `CSTNode`.
        self.attribute_annotations[".".join(self.qualifier)] = annotation_value
        return True

    def leave_AnnAssign(self, node: cst.AnnAssign) -> None:
        self.qualifier.pop()

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        module = node.module
        if module is None or isinstance(node, cst.ImportStar):
            return
        # pyre-fixme[6]: Expected `List[CSTNode]` for 1st param but got
        #  `Union[Sequence[ImportAlias], ImportStar]`.
        # pyre-fixme[6]: Expected `str` for 1st param but got `Union[BaseExpression,
        #  str]`.
        self._add_to_imports(node.names, cst.Name(module.value), module.value)

    def _create_import_from_annotation(self, returns: cst.CSTNode) -> cst.CSTNode:
        # pyre-fixme[16]: `CSTNode` has no attribute `annotation`.
        if isinstance(returns.annotation, cst.Attribute):
            annotation = returns.annotation
            key = _get_attribute_as_string(annotation.value)
            self._add_to_imports(
                [cst.ImportAlias(name=annotation.attr)], annotation.value, key
            )
            return cst.Annotation(annotation=returns.annotation.attr)
        else:
            return returns

    def _add_to_imports(
        self, names: List[cst.CSTNode], module: Union[cst.Name, cst.Attribute], key: str
    ) -> None:
        if key not in self.imports:
            self.imports[key] = ImportStatement(names=names, module=module)
        else:
            import_statement = self.imports[key]
            for name in names:
                if not _is_in_list(import_statement.names, name):
                    import_statement.names.append(name)


class TypeTransformer(cst.CSTTransformer):
    def __init__(
        self,
        function_annotations: Dict[str, FunctionAnnotation],
        attribute_annotations: Dict[str, cst.Annotation],
        imports: Dict[str, ImportStatement],
    ) -> None:
        # Qualifier for storing the canonical name of the current function.
        self.qualifier: List[str] = []
        # Store the annotations.
        self.function_annotations = function_annotations
        self.attribute_annotations = attribute_annotations
        self.toplevel_annotations: Dict[str, cst.CSTNode] = {}
        self.imports = imports
        self.import_statements: List[cst.CSTNode] = []

    def _qualifier_name(self) -> str:
        return ".".join(self.qualifier)

    def _annotate_single_target(
        self, node: cst.Assign, updated_node: cst.Assign
    ) -> Union[cst.Assign, cst.AnnAssign]:
        if isinstance(node.targets[0].target, cst.Tuple):
            target = node.targets[0].target
            # pyre-fixme[16]: `BaseAssignTargetExpression` has no attribute `elements`.
            for element in target.elements:
                if not isinstance(element.value, cst.Subscript):
                    name = _get_name_as_string(element.value.value)
                    self._add_to_toplevel_annotations(name)
            return updated_node
        else:
            target = node.targets[0].target
            # pyre-fixme[16]: `BaseAssignTargetExpression` has no attribute `value`.
            name = _get_name_as_string(target.value)
            self.qualifier.append(name)
            if self._qualifier_name() in self.attribute_annotations and not isinstance(
                target, cst.Subscript
            ):
                annotation = self.attribute_annotations[self._qualifier_name()]
                self.qualifier.pop()
                return cst.AnnAssign(cst.Name(name), annotation, node.value)
            else:
                self.qualifier.pop()
                return updated_node

    def _split_module(
        self, module: cst.Module, updated_module: cst.Module
    ) -> Tuple[
        List[Union[cst.SimpleStatementLine, cst.BaseCompoundStatement]],
        List[Union[cst.SimpleStatementLine, cst.BaseCompoundStatement]],
    ]:
        import_add_location = 0
        # This works under the principle that while we might modify node contents,
        # we have yet to modify the number of statements. So we can match on the
        # original tree but break up the statements of the modified tree. If we
        # change this assumption in this visitor, we will have to change this code.
        for i, statement in enumerate(module.body):
            if isinstance(statement, cst.SimpleStatementLine):
                for possible_import in statement.body:
                    for last_import in self.import_statements:
                        if possible_import is last_import:
                            import_add_location = i + 1
                            break

        return (
            list(updated_module.body[:import_add_location]),
            list(updated_module.body[import_add_location:]),
        )

    def _add_to_toplevel_annotations(self, name: str) -> None:
        self.qualifier.append(name)
        if self._qualifier_name() in self.attribute_annotations:
            annotation = self.attribute_annotations[self._qualifier_name()]
            self.toplevel_annotations[name] = annotation
        self.qualifier.pop()

    def _update_default_parameters(
        self, annotations: FunctionAnnotation, updated_node: cst.FunctionDef
    ) -> cst.Parameters:
        parameter_annotations = {}
        annotated_default_parameters = []
        for parameter in list(annotations.parameters.default_params):
            if parameter.annotation:
                parameter_annotations[parameter.name.value] = parameter.annotation
        for parameter in list(updated_node.params.default_params):
            if parameter.name.value in parameter_annotations:
                annotated_default_parameters.append(
                    parameter.with_changes(
                        annotation=parameter_annotations[parameter.name.value]
                    )
                )
            else:
                annotated_default_parameters.append(parameter)
        return annotations.parameters.with_changes(
            default_params=annotated_default_parameters
        )

    def _insert_empty_line(
        self,
        statements: List[Union[cst.SimpleStatementLine, cst.BaseCompoundStatement]],
    ) -> List[Union[cst.SimpleStatementLine, cst.BaseCompoundStatement]]:
        if len(statements) < 1:
            # No statements, nothing to add to
            return statements
        if len(statements[0].leading_lines) == 0:
            # Statement has no leading lines, add one!
            return [
                statements[0].with_changes(leading_lines=(cst.EmptyLine(),)),
                *statements[1:],
            ]
        if statements[0].leading_lines[0].comment is None:
            # First line is empty, so its safe to leave as-is
            return statements
        # Statement has a comment first line, so lets add one more empty line
        return [
            statements[0].with_changes(
                leading_lines=(cst.EmptyLine(), *statements[0].leading_lines)
            ),
            *statements[1:],
        ]

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self.qualifier.append(node.name.value)

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        self.qualifier.pop()
        return updated_node

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self.qualifier.append(node.name.value)
        # pyi files don't support inner functions, return False to stop the traversal.
        return False

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        key = self._qualifier_name()
        self.qualifier.pop()
        if key in self.function_annotations:
            annotations = self.function_annotations[key]
            # Only add new annotation if one doesn't already exist
            if not updated_node.returns:
                updated_node = updated_node.with_changes(returns=annotations.returns)
            # Don't override default values when annotating functions
            new_parameters = self._update_default_parameters(annotations, updated_node)
            return updated_node.with_changes(params=new_parameters)
        return updated_node

    def leave_Assign(
        self, original_node: cst.Assign, updated_node: cst.Assign
    ) -> Union[cst.Assign, cst.AnnAssign]:

        if len(original_node.targets) > 1:
            for assign in original_node.targets:
                if not isinstance(assign.target, cst.Subscript):
                    self._add_to_toplevel_annotations(
                        # pyre-fixme[16]: `BaseAssignTargetExpression` has no
                        #  attribute `value`.
                        _get_name_as_string(assign.target.value)
                    )
            return updated_node
        else:
            return self._annotate_single_target(original_node, updated_node)

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom:
        self.import_statements.append(original_node)
        # pyre-fixme[6]: Expected `Union[Attribute, Name]` for 1st param but got
        #  `Optional[Union[Attribute, Name]]`.
        key = _get_attribute_as_string(original_node.module)
        if (
            original_node.module is not None
            # pyre-fixme[16]: `Optional` has no attribute `value`.
            and original_node.module.value in self.imports
        ):
            names = list(updated_node.names) + self.imports[key].names
            updated_node = updated_node.with_changes(names=tuple(names))
            del self.imports[key]
        return updated_node

    def visit_ImportAlias(self, node: cst.ImportAlias) -> None:
        self.import_statements.append(node)

    def visit_ImportStar(self, node: cst.ImportStar) -> None:
        self.import_statements.append(node)

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        if not self.toplevel_annotations and not self.imports:
            return updated_node

        toplevel_statements = []

        # First, find the insertion point for imports
        statements_before_imports, statements_after_imports = self._split_module(
            original_node, updated_node
        )

        # Make sure there's at least one empty line before the first non-import
        statements_after_imports = self._insert_empty_line(statements_after_imports)

        for _, import_statement in self.imports.items():
            import_statement = cst.ImportFrom(
                module=import_statement.module,
                # pyre-fixme[6]: Expected `Union[Sequence[ImportAlias], ImportStar]`
                #  for 2nd param but got `List[ImportFrom]`.
                names=import_statement.names,
            )
            # Add import statements to module body.
            # Need to assign an Iterable, and the argument to SimpleStatementLine
            # must be subscriptable.
            toplevel_statements.append(cst.SimpleStatementLine([import_statement]))

        for name, annotation in self.toplevel_annotations.items():
            annotated_assign = cst.AnnAssign(
                cst.Name(name),
                # pyre-fixme[16]: `CSTNode` has no attribute `annotation`.
                cst.Annotation(annotation.annotation),
                None,
            )
            toplevel_statements.append(cst.SimpleStatementLine([annotated_assign]))

        return updated_node.with_changes(
            body=[
                *statements_before_imports,
                *toplevel_statements,
                *statements_after_imports,
            ]
        )


# pyre-fixme[2]: Parameter annotation cannot contain `Any`.
def _parse(file: IO[Any]) -> cst.Module:
    contents = file.read()
    return cst.parse_module(contents)


def _annotate_source(stubs: cst.Module, source: cst.Module) -> cst.Module:
    visitor = TypeCollector()
    stubs.visit(visitor)
    transformer = TypeTransformer(
        visitor.function_annotations, visitor.attribute_annotations, visitor.imports
    )
    return source.visit(transformer)


def apply_stub_annotations(stub_path: str, file_path: str) -> str:
    with open(stub_path) as stub_file, open(file_path) as source_file:
        stubs = _parse(stub_file)
        source = _parse(source_file)
        modified_tree = _annotate_source(stubs, source)
        return modified_tree.code