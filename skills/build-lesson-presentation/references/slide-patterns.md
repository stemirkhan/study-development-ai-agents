# Библиотека слайдов

Копировать паттерн внутрь authoring-блока общего шаблона и менять содержимое.
На каждом слайде сохранять уникальный `id`, `data-title` и заголовок.

## Concept

Использовать для определения механизма и его границ:

```html
<section class="slide" id="concept" data-title="Контракт">
  <div class="slide__content">
    <p class="eyebrow">Mental model</p>
    <h2>Один механизм — один проверяемый контракт</h2>
    <p class="lead">Короткое объяснение, зачем механизм существует.</p>
    <div class="callout">
      <strong>Инвариант</strong>
      <p>Условие, которое должно сохраняться при любом переходе.</p>
    </div>
  </div>
</section>
```

## Comparison

Использовать для baseline против agentic-варианта:

```html
<div class="comparison">
  <article class="panel">
    <p class="panel__label">Deterministic baseline</p>
    <h3>Workflow</h3>
    <ul class="clean-list"><li>Наблюдаемое преимущество</li></ul>
  </article>
  <article class="panel panel--accent">
    <p class="panel__label">Agent runtime</p>
    <h3>Bounded loop</h3>
    <ul class="clean-list"><li>Цена и новый failure mode</li></ul>
  </article>
</div>
```

## Flow

Использовать HTML-узлы и стрелки либо небольшой inline SVG. Всегда добавлять
текстовый эквивалент:

```html
<div class="flow" aria-label="Planner передаёт план Executor, результат меняет State">
  <div class="flow__node"><strong>Planner</strong><span>Plan</span></div>
  <span class="flow__arrow" aria-hidden="true">→</span>
  <div class="flow__node"><strong>Executor</strong><span>Effect</span></div>
  <span class="flow__arrow" aria-hidden="true">→</span>
  <div class="flow__node"><strong>State</strong><span>Event</span></div>
</div>
```

## Code and contract

Показывать только фрагмент, нужный для обсуждаемого решения:

```html
<div class="code-layout">
  <pre><code>class Tool(Protocol):
    async def invoke(self, call: ToolCall) -&gt; ToolResult: ...</code></pre>
  <div>
    <h3>Что здесь важно</h3>
    <ul class="check-list">
      <li>Validation до side effect</li>
      <li>Deadline является частью контракта</li>
    </ul>
  </div>
</div>
```

## Progressive reveal

Добавлять `data-fragment` только когда порядок раскрытия несёт смысл:

```html
<ol class="sequence">
  <li data-fragment>Сначала зафиксировать intent.</li>
  <li data-fragment>Затем выполнить effect.</li>
  <li data-fragment>После подтверждения записать checkpoint.</li>
</ol>
```

Не использовать fragments для обычных списков.

## Lab

```html
<div class="lab-card">
  <p class="eyebrow">Практика · 35 минут</p>
  <h2>Реализовать bounded executor</h2>
  <div class="lab-grid">
    <div><h3>Ограничения</h3><ul class="clean-list">...</ul></div>
    <div><h3>Acceptance</h3><ul class="check-list">...</ul></div>
  </div>
</div>
```

## Gate

Завершать презентацию наблюдаемыми доказательствами:

```html
<div class="gate">
  <h2>Gate: механизм доказан</h2>
  <ul class="check-list">
    <li>Contract tests воспроизводимы без live LLM</li>
    <li>Fault injection подтверждает bounded termination</li>
    <li>Студент защищает trade-offs</li>
  </ul>
</div>
```
