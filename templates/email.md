{#- The daily email as Markdown, for Andy to edit and paste into Substack.
    Kept simple on purpose: the Desktop "Copy for Substack" button converts exactly these
    pieces (## headings, - bullets, **bold**, *italic*, [links](url)) into formatting. -#}
All UK Tech, {{ date_long }}

[Your opening note: two or three sentences on what's big today.]

Andy
{% if top %}
## Top story

**[{{ top.headline | md }}]({{ top.link }})**
{{ top.link_outlet.name }}{% if top.paywall %} (£){% endif %}, {{ top.source_count }} source{{ 's' if top.source_count != 1 }}

**Why it matters:** [Your line.{% if top.so_what %} AI suggests: {{ top.so_what | md }}{% endif %}]
{% endif %}
{% for sec in sections %}{% if sec.stories %}{% set pick = sec.stories[0] %}
## {{ sec.name }}

**Andy's Pick: {% if pick.is_update %}Update: {% endif %}[{{ pick.headline | md }}]({{ pick.link }})** ({{ pick.link_outlet.name }}{% if pick.paywall %}, £{% endif %}{% if pick.source_count > 1 %}, {{ pick.source_count }} sources{% endif %}){% if pick.so_what %}
*So what: {{ pick.so_what | md }}*{% endif %}
{% if sec.stories|length > 1 %}
{% for s in sec.stories[1:] %}- {% if s.is_update %}Update: {% endif %}[{{ s.headline | md }}]({{ s.link }}) ({{ s.link_outlet.name }}{% if s.paywall %}, £{% endif %})
{% endfor %}{% endif %}
{% endif %}{% endfor %}
More UK tech news, updated every hour, at [alluktech.com]({{ site.url }}).
