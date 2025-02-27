
import sys
import os
import typing as tp
# We need this sys.path line for running this example, especially in VSCode debugger.
sys.path.insert(0, os.path.join(sys.path[0], '..'))

import edifice as ed

from edifice.qt import QT_VERSION
if QT_VERSION == "PyQt6" and not tp.TYPE_CHECKING:
    from PyQt6.QtGui import QValidator
    from PyQt6.QtCore import Qt
else:
    from PySide6.QtGui import QValidator
    from PySide6.QtCore import Qt

@ed.component
def Main(self):


    mltext, mltext_set = ed.use_state("Hello World")
    ddoptions, ddoptionss_set = ed.use_state(0)
    ddoptions2, ddoptions2_set = ed.use_state(0)
    ddoptions3, ddoptions3_set = ed.use_state(0)
    sival, sival_set = ed.use_state(0)

    with ed.Window():
        ed.Label("Hello")
        ed.Label(
            text= "World",
            selectable=True,
        )
        with ed.View(layout="row"):
            ed.Dropdown(
                options=["Option set 1", "Option set 2"],
                selection=ddoptions,
                on_select=ddoptionss_set,
                enable_mouse_scroll=False,
            )
            match ddoptions:
                case 0:
                    ed.Dropdown(
                        options=["Option set 1, 1", "Option set 1, 2", "Option set 1, 3"],
                        selection=ddoptions2,
                        on_select=ddoptions2_set,
                        enable_mouse_scroll=False,
                    )
                case 1:
                    ed.Dropdown(
                        options=["Option set 2, 1", "Option set 2, 2", "Option set 2, 3"],
                        selection=ddoptions3,
                        on_select=ddoptions3_set,
                        enable_mouse_scroll=False,
                    )
        with ed.View(layout="row", style={"margin": 10}):
            ed.TextInputMultiline(
                text=mltext,
                on_change=mltext_set,
                placeholder_text="Type here",
                style={
                    "min-height": 100,
                    "border": "1px solid black",
                    "font-size": "20px",
                    "font-family": "Courier New",
                    "font-style": "italic",
                }
            )
            ed.Button("Exclaim text", on_click=lambda _: mltext_set("!" + mltext + "!"))
        with ed.View():
            ed.SpinInput(
                value=sival,
                min_value=10,
                max_value=20,
                on_change=lambda v: (
                    sival_set(v)
                ),
            )

if __name__ == "__main__":
    ed.App(Main()).start()
