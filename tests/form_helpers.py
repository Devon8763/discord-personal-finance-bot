import discord

def fill(field,value):
    if isinstance(field,discord.ui.Select):
        index=next(n for n,(label,actual) in enumerate(field.choices) if actual==value or label==value)
        field._values=[str(index)]
    else:
        field._value=str(value)


