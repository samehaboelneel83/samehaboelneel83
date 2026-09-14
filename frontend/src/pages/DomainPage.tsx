import { useEffect, useState } from "react";

import { api } from "../lib/api";
import { Card, Empty, Notice } from "../components/common";

/**
 * The domain model is what problems are stated *about*. Keeping it separate
 * means the same depots and crews can feed a routing problem today and a
 * rostering problem tomorrow without being typed in again.
 */
export default function DomainPage() {
  const [types, setTypes] = useState<Array<{ key: string; name: string; entities: number }>>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [entities, setEntities] = useState<
    Array<{ key: string; name: string; entity_type: string; attributes: Record<string, unknown> }>
  >([]);
  const [error, setError] = useState<string | null>(null);
  const [draftType, setDraftType] = useState({ key: "", name: "" });
  const [draftEntity, setDraftEntity] = useState({ key: "", name: "", attributes: "{}" });

  const loadTypes = () =>
    api
      .entityTypes()
      .then((body) => setTypes(body.entity_types))
      .catch((e) => setError(String(e.message)));

  useEffect(() => {
    void loadTypes();
  }, []);

  useEffect(() => {
    if (!selected) {
      setEntities([]);
      return;
    }
    api
      .entities(selected)
      .then((body) => setEntities(body.entities))
      .catch((e) => setError(String(e.message)));
  }, [selected]);

  async function addType() {
    setError(null);
    try {
      await api.createEntityType(draftType.key.trim(), draftType.name.trim() || draftType.key);
      setDraftType({ key: "", name: "" });
      await loadTypes();
    } catch (e) {
      setError(String((e as Error).message));
    }
  }

  async function addEntity() {
    if (!selected) return;
    setError(null);
    try {
      let attributes: Record<string, unknown> = {};
      try {
        attributes = JSON.parse(draftEntity.attributes || "{}");
      } catch {
        setError("Attributes must be valid JSON.");
        return;
      }
      await api.createEntity({
        entity_type: selected,
        key: draftEntity.key.trim(),
        name: draftEntity.name.trim() || draftEntity.key,
        attributes,
      });
      setDraftEntity({ key: "", name: "", attributes: "{}" });
      const body = await api.entities(selected);
      setEntities(body.entities);
      await loadTypes();
    } catch (e) {
      setError(String((e as Error).message));
    }
  }

  return (
    <>
      {error && <Notice kind="bad">{error}</Notice>}

      <div className="grid two">
        <Card
          title="Entity types"
          subtitle="The kinds of thing this organisation reasons about."
        >
          {types.length === 0 ? (
            <Empty>No entity types yet. Add one below to start a domain model.</Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Name</th>
                  <th className="num">Entities</th>
                </tr>
              </thead>
              <tbody>
                {types.map((t) => (
                  <tr
                    key={t.key}
                    className={`clickable ${selected === t.key ? "selected" : ""}`}
                    onClick={() => setSelected(t.key)}
                  >
                    <td className="mono">{t.key}</td>
                    <td>{t.name}</td>
                    <td className="num">{t.entities}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="row" style={{ marginTop: "0.75rem" }}>
            <input
              type="text"
              placeholder="key, e.g. depot"
              value={draftType.key}
              onChange={(e) => setDraftType({ ...draftType, key: e.target.value })}
            />
            <input
              type="text"
              placeholder="name"
              value={draftType.name}
              onChange={(e) => setDraftType({ ...draftType, name: e.target.value })}
            />
            <button className="secondary" onClick={addType} disabled={!draftType.key.trim()}>
              Add type
            </button>
          </div>
        </Card>

        <Card
          title={selected ? `Entities: ${selected}` : "Entities"}
          subtitle={
            selected
              ? "These become the elements of an index set a problem can be stated over."
              : "Select an entity type to see its entities."
          }
        >
          {!selected ? (
            <Empty>No entity type selected.</Empty>
          ) : entities.length === 0 ? (
            <Empty>No entities of this type yet.</Empty>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>Name</th>
                    <th>Attributes</th>
                  </tr>
                </thead>
                <tbody>
                  {entities.map((e) => (
                    <tr key={e.key}>
                      <td className="mono">{e.key}</td>
                      <td>{e.name}</td>
                      <td className="mono muted">{JSON.stringify(e.attributes)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {selected && (
            <div className="row" style={{ marginTop: "0.75rem" }}>
              <input
                type="text"
                placeholder="key"
                value={draftEntity.key}
                onChange={(e) => setDraftEntity({ ...draftEntity, key: e.target.value })}
              />
              <input
                type="text"
                placeholder="name"
                value={draftEntity.name}
                onChange={(e) => setDraftEntity({ ...draftEntity, name: e.target.value })}
              />
              <input
                type="text"
                placeholder='{"capacity": 100}'
                value={draftEntity.attributes}
                onChange={(e) => setDraftEntity({ ...draftEntity, attributes: e.target.value })}
              />
              <button className="secondary" onClick={addEntity} disabled={!draftEntity.key.trim()}>
                Add entity
              </button>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
