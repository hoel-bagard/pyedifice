import contextlib
import inspect
import logging
import queue
import time
import typing as tp

from .component import Component, PropsDict, register_props, BaseComponent, RootComponent


class _ChangeManager(object):
    def __init__(self):
        self.changes = []

    def set(self, obj, key, value):
        old_value = None
        if hasattr(obj, key):
            old_value = getattr(obj, key)
        self.changes.append((obj, key, hasattr(obj, key), old_value, value))
        setattr(obj, key, value)

    def unwind(self):
        for obj, key, had_key, old_value, new_value in reversed(self.changes):
            if had_key:
                setattr(obj, key, old_value)
            else:
                try:
                    delattr(obj, key)
                except AttributeError:
                    logging.warning(
                        "Error while unwinding changes: Unable to delete %s from %s. Setting to None instead" % (
                            key, obj.__class__.__name__))
                    setattr(obj, key, None)

@contextlib.contextmanager
def _storage_manager():
    changes = _ChangeManager()
    try:
        yield changes
    except Exception as e:
        changes.unwind()
        raise e


class _WidgetTree(object):
    def __init__(self, component, children):
        self.component = component
        self.children = children

    def _dereference(self, address):
        qt_tree = self
        for index in address:
            qt_tree = qt_tree.children[index]
        return qt_tree

    def gen_qt_commands(self, render_context):
        commands = []
        for child in self.children:
            rendered = child.gen_qt_commands(render_context)
            commands.extend(rendered)

        if not render_context.need_rerender(self.component):
            return commands

        old_props = render_context.get_old_props(self.component)
        new_props = PropsDict({k: v for k, v in self.component.props._items if k not in old_props or old_props[k] != v})
        commands.extend(self.component._qt_update_commands(self.children, new_props, {}))
        return commands

    def __hash__(self):
        return id(self)

    def print_tree(self, indent=0):
        tags = self.component._tags()
        if self.children:
            print("\t" * indent + tags[0])
            for child in self.children:
                child.print_tree(indent=indent + 1)
            print("\t" * indent + tags[1])
        else:
            print("\t" * indent + tags[2])


class _RenderContext(object):
    def __init__(self, storage_manager, force_refresh=False):
        self.storage_manager = storage_manager
        self.need_qt_command_reissue = {}
        self.component_to_new_props = {}
        self.component_to_old_props = {}
        self.force_refresh = force_refresh

        self._callback_queue = []

    def schedule_callback(self, callback, args=None, kwargs=None):
        args = args or []
        kwargs = kwargs or {}
        self._callback_queue.append((callback, args, kwargs))

    def run_callbacks(self):
        for callback, args, kwargs in self._callback_queue:
            callback(*args, **kwargs)

    def mark_props_change(self, component, newprops, new_component=False):
        d = dict(newprops._items)
        if "children" not in d:
            d["children"] = []
        self.component_to_new_props[component] = newprops
        if component not in self.component_to_old_props:
            if new_component:
                self.component_to_old_props[component] = PropsDict({})
            else:
                self.component_to_old_props[component] = component.props
        self.set(component, "_props", d)

    def get_new_props(self, component):
        if component in self.component_to_new_props:
            return self.component_to_new_props[component]
        return component.props

    def get_old_props(self, component):
        if component in self.component_to_old_props:
            return self.component_to_old_props[component]
        return PropsDict({})

    def commit(self):
        for component, newprops in self.component_to_new_props.items():
            component.register_props(newprops)

    def set(self, obj, k, v):
        self.storage_manager.set(obj, k, v)

    def mark_qt_rerender(self, component, need_rerender):
        self.need_qt_command_reissue[component] = need_rerender

    def need_rerender(self, component):
        return self.need_qt_command_reissue.get(component, False)


class RenderEngine(object):

    def __init__(self, root, app=None):
        self._component_tree = {}
        self._widget_tree = {}
        self._root = root

        self._app = app
        self._first_render = True
        self._nrenders = 0
        self._render_time = 0
        self._last_render_time = 0
        self._worst_render_time = 0

    def _delete_component(self, component, recursive):
        # Delete component from render trees
        sub_components = self._component_tree[component]
        if recursive:
            if isinstance(sub_components, Component):
                self._delete_component(sub_components, recursive)
            else:
                for sub_comp in sub_components:
                    self._delete_component(sub_comp, recursive)
            # Node deletion

        component.will_unmount()
        del self._component_tree[component]
        del self._widget_tree[component]

    def _refresh_by_class(self, classes):
        # Algorithm:
        # 1) Find all old components that's not a child of another component

        # TODO: handle changes in the tree root
        components_to_replace = [] # List of pairs: (old_component, new_component_class, parent component, new_component)
        old_components = [cls for cls, _ in classes]
        new_components = [cls for _, cls in classes]
        def traverse(comp, parent):
            if comp.__class__ in old_components:
                new_component_class = [new_cls for old_cls, new_cls in classes if old_cls == comp.__class__][0]
                if new_component_class is None:
                    raise ValueError("Error after updating code: cannot find class %s" % comp.__class__)
                components_to_replace.append([comp, new_component_class, parent, None])
                return
            sub_components = self._component_tree[comp]
            if isinstance(sub_components, list):
                for sub_comp in sub_components:
                    traverse(sub_comp, comp)
            else:
                traverse(sub_components, comp)

        traverse(self._root, None)
        # 2) For all such old components, construct a new component and merge in old component props
        for parts in components_to_replace:
            old_comp, new_comp_class, _, _ = parts

            try:
                kwargs = {k: old_comp.props[k] for k, v in list(inspect.signature(new_comp_class.__init__).parameters.items())[1:]
                          if v.default is inspect.Parameter.empty and k[0] != "_"}
            except KeyError:
                raise ValueError("Error while reloading %s: New class expects props not present in old class" % old_comp)
            parts[3] = new_comp_class(**kwargs)
            parts[3]._props.update(old_comp._props)
            if hasattr(old_comp, "_key"):
                parts[3]._key = old_comp._key

        # 3) Replace old component in the place in the tree where they first appear, with a reference to new component
        for old_comp, _, parent_comp, new_comp in components_to_replace:
            if isinstance(self._component_tree[parent_comp], list):
                for i, comp in enumerate(parent_comp.children):
                    if comp is old_comp:
                        parent_comp._props["children"][i] = new_comp

        # 5) call _render for all new component parents
        render_commands = self._request_rerender([parent_comp for _, _, parent_comp, _ in components_to_replace], PropsDict({}), {})
        # 4) Delete all old_components from the tree, and do this recursively
        for old_comp, _, _, _ in components_to_replace:
            if old_comp in self._component_tree:
                self._delete_component(old_comp, recursive=True)
        return render_commands


    def _update_old_component(self, component, newprops, render_context: _RenderContext):
        if component.should_update(newprops, {}):
            render_context.mark_props_change(component, newprops)
            rerendered_obj = self._render(component, render_context)

            render_context.mark_qt_rerender(rerendered_obj.component, True)
            return rerendered_obj

        render_context.mark_props_change(component, newprops)
        render_context.mark_qt_rerender(component, False)
        # need_qt_command_reissue[self._widget_tree[component].component] = False
        return self._widget_tree[component]

    def _get_child_using_key(self, d, key, newchild, render_context: _RenderContext):
        if key not in d or d[key].__class__ != newchild.__class__:
            return newchild # self._render(newchild, storage_manager, need_qt_command_reissue)
        self._update_old_component(d[key], newchild.props, render_context)
        return d[key]

    def _attach_keys(self, component, render_context: _RenderContext):
        for i, child in enumerate(component.children):
            if not hasattr(child, "_key"):
                logging.warning("Setting child key to: KEY" + str(i))
                render_context.set(child, "_key", "KEY" + str(i))

    def _render_base_component(self, component, render_context):
        if len(component.children) > 1:
            self._attach_keys(component, render_context)
        if component not in self._component_tree:
            # New component, simply render everything
            self._component_tree[component] = list(component.children)
            rendered_children = [self._render(child, render_context) for child in component.children]
            self._widget_tree[component] = _WidgetTree(component, rendered_children) 
            render_context.mark_qt_rerender(component, True)
            return self._widget_tree[component]
        else:
            # Figure out which children are pre-existing
            old_children = self._component_tree[component]
            if len(old_children) > 1:
                self._attach_keys(component, render_context)

            if len(component.children) == 1 and len(old_children) == 1:
                if component.children[0].__class__ == old_children[0].__class__:
                    self._update_old_component(old_children[0], component.children[0].props, render_context)
                    children = [old_children[0]]
                else:
                    children = [component.children[0]]
            else:
                if len(old_children) == 1:
                    if not hasattr(old_children[0], "_key"):
                        render_context.set(old_children[0], "_key", "KEY0")
                key_to_old_child = {child._key: child for child in old_children}
                old_child_to_props = {child: child.props for child in old_children}

                # Children contains all the future children of the component:
                # a mixture of old components (if they can be updated) and new ones
                children = [self._get_child_using_key(key_to_old_child, new_child._key, new_child, render_context)
                            for new_child in component.children]
            # TODO: What if children key order changed??
            rendered_children = []
            parent_needs_rerendering = False
            for child1, child2 in zip(children, component.children):
                # child1 == child2 if they are both new, i.e. no old child matches child1
                # This component would then need to be updated to draw said new child
                # Otherwise, no component has been added, so no re-rendering is necessary
                if child1 != child2:
                    rendered_children.append(self._widget_tree[child1])
                else:
                    parent_needs_rerendering = True
                    rendered_children.append(self._render(child1, render_context))
            if parent_needs_rerendering:
                render_context.mark_qt_rerender(component, True)

            children_set = set(children)
            for old_child in old_children:
                if old_child not in children_set:
                    self._delete_component(old_child, True)

            self._component_tree[component] = children
            self._widget_tree[component] = _WidgetTree(component, rendered_children) 
            props_dict = dict(component.props._items)
            props_dict["children"] = list(children)
            render_context.mark_props_change(component, PropsDict(props_dict))
            return self._widget_tree[component]

    def _render(self, component: Component, render_context: _RenderContext):
        component._controller = self._app
        if isinstance(component, BaseComponent):
            self._render_base_component(component, render_context)

        sub_component = component.render()
        old_rendering = None
        if component in self._component_tree:
            old_rendering = self._component_tree[component]

        if sub_component.__class__ == old_rendering.__class__:
            # TODO: Update component will receive props
            # TODO figure out if its subcomponent state or old_rendering state
            self._widget_tree[component] = self._update_old_component(
                old_rendering, sub_component.props, render_context)
        else:
            if old_rendering is not None:
                self._delete_component(sub_component, True)
            else:
                render_context.schedule_callback(component.did_mount)
            self._component_tree[component] = sub_component
            self._widget_tree[component] = self._render(sub_component, render_context)

        return self._widget_tree[component]

    def _gen_all_commands(self, qt_trees, render_context):
        ret = []
        for qt_tree in qt_trees:
            qt_commands = qt_tree.gen_qt_commands(render_context)
            root = qt_tree.component
            ret.append((root, (qt_tree, qt_commands)))
        return ret

    def _gen_widget_trees(self, components):
        qt_trees = []
        with _storage_manager() as storage_manager:
            render_context = _RenderContext(storage_manager)
            for component in components:
                qt_tree = self._render(component, render_context)
                qt_trees.append(qt_tree)
        return qt_trees, render_context

    def _do_otherstuff(self, components):
        start_time = time.process_time()
        qt_trees, render_context = self._gen_widget_trees(components)

        # qt_tree.print_tree()
        # for component, need_rerendering in render_context.need_qt_command_reissue.items():
        #     if need_rerendering:
        #         logging.warn("Rerendering: %s", component)
        if start_time - self._last_render_time > 1:
            logging.info("Time without rendering: %.2f ms", (time.process_time() - start_time) * 1000)


        if not self._first_render:
            end_time = time.process_time()
            self._render_time += (end_time - start_time)
            self._worst_render_time = max(end_time - start_time, self._worst_render_time)
            self._nrenders += 1

            if end_time - self._last_render_time > 1:
                logging.info("Rendered %d times, with average render time of %.2f ms and worst render time of %.2f ms",
                             self._nrenders, 1000 * self._render_time / self._nrenders, 1000 * self._worst_render_time)
                self._last_render_time = end_time
        self._first_render = False

        render_context.run_callbacks()
        return ret

    def _request_rerender(self, components, newprops, newstate, execute=True):
        self._do_otherstuff(components)

