"""Helpers for the user-invoked ``/<skill-name>`` dispatch path.

The input module's wildcard dispatcher
(:meth:`kohakuterrarium.modules.input.base.BaseInputModule._dispatch_skill_slash`)
reuses :func:`build_user_skill_turn` to compose the user-turn text
injected into the conversation when a user types ``/my-skill arg1``.

Keeping this in its own small module avoids a circular import between
:mod:`kohakuterrarium.modules.input.base` and
:mod:`kohakuterrarium.builtins.user_commands` (which indirectly depends
on the input module at agent init).
"""

from kohakuterrarium.skills.registry import Skill


def build_user_skill_turn(skill: Skill, arguments: str) -> str:
    """Render a user-turn preamble that asks the model to follow ``skill``.

    Shape (chosen to be obvious to any model family):

    .. code-block:: text

        Please follow the "<name>" skill:

        <SKILL.md body>

        Arguments the user provided: <arguments>

    The argument line is omitted when ``arguments`` is empty.
    """
    header = f'Please follow the "{skill.name}" skill:\n\n'
    body = skill.body.rstrip() if skill.body else ""
    arg_line = f"\n\nArguments the user provided: {arguments}" if arguments else ""
    return f"{header}{body}{arg_line}"
