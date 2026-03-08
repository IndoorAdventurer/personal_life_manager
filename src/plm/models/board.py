from pydantic import BaseModel, Field, model_validator

from plm.models.card import KanbanCard
from plm.models.column import KanbanColumn


class KanbanBoard(BaseModel):
    columns: list[KanbanColumn] = Field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Invariant: a board must always have at least one WIP column.       #
    # Enforced here (not in the storage layer) so that any deserialized  #
    # board is also checked — not just freshly created ones.             #
    # ------------------------------------------------------------------ #
    @model_validator(mode="after")
    def require_wip_column(self) -> "KanbanBoard":
        if self.columns and not any(col.is_wip for col in self.columns):
            raise ValueError("KanbanBoard must have at least one WIP column (is_wip=True)")
        return self

    # ------------------------------------------------------------------ #
    # Helper methods — kept on the model so business logic stays co-     #
    # located with the data it operates on.                              #
    # ------------------------------------------------------------------ #

    def find_card(self, card_id: str) -> tuple[KanbanColumn, KanbanCard] | None:
        """Return (column, card) for the given card_id, or None if not found."""
        for col in self.columns:
            for card in col.cards:
                if card.id == card_id:
                    return col, card
        return None

    def get_wip_cards(self) -> list[KanbanCard]:
        """Return all cards that are currently in a WIP column."""
        return [
            card
            for col in self.columns
            if col.is_wip
            for card in col.cards
        ]

    def move_card(self, card_id: str, target_column_id: str, position: int | None = None) -> None:
        """
        Move a card to a different column.

        position=None appends to the end of the target column.
        Raises ValueError if card or target column is not found.
        """
        result = self.find_card(card_id)
        if result is None:
            raise ValueError(f"Card {card_id!r} not found on this board")

        source_col, card = result

        target_col = next((c for c in self.columns if c.id == target_column_id), None)
        if target_col is None:
            raise ValueError(f"Column {target_column_id!r} not found on this board")

        source_col.cards.remove(card)

        if position is None:
            target_col.cards.append(card)
        else:
            # Clamp position to valid range so callers don't need to guard it
            position = max(0, min(position, len(target_col.cards)))
            target_col.cards.insert(position, card)

    def reorder_cards(self, column_id: str, card_ids: list[str]) -> None:
        """
        Reorder cards within a column by providing the desired id order.

        card_ids must contain exactly the same ids as the column currently
        holds — raises ValueError otherwise to prevent accidental data loss.
        """
        col = next((c for c in self.columns if c.id == column_id), None)
        if col is None:
            raise ValueError(f"Column {column_id!r} not found on this board")

        current_ids = {card.id for card in col.cards}
        if set(card_ids) != current_ids:
            raise ValueError(
                "card_ids must contain exactly the same ids as the column currently holds"
            )

        card_map = {card.id: card for card in col.cards}
        col.cards = [card_map[cid] for cid in card_ids]

    def add_column(self, name: str, is_wip: bool = False, position: int | None = None) -> KanbanColumn:
        """
        Add a new empty column to the board.

        position=None appends to the end. Returns the new column so the
        caller can reference its id without a second lookup.
        """
        col = KanbanColumn(name=name, is_wip=is_wip)
        if position is None:
            self.columns.append(col)
        else:
            position = max(0, min(position, len(self.columns)))
            self.columns.insert(position, col)
        return col

    def rename_column(self, column_id: str, name: str) -> None:
        """Rename a column. Raises ValueError if the column is not found."""
        col = next((c for c in self.columns if c.id == column_id), None)
        if col is None:
            raise ValueError(f"Column {column_id!r} not found on this board")
        col.name = name

    def remove_column(self, column_id: str, force: bool = False) -> None:
        """
        Remove a column from the board.

        - Raises ValueError if the column is not found.
        - Raises ValueError if the column is the last WIP column — removing
          it would violate the board invariant, so force has no effect here.
        - Raises ValueError if the column has cards and force=False.
        - If force=True, the column and all its cards are deleted silently.
        """
        col = next((c for c in self.columns if c.id == column_id), None)
        if col is None:
            raise ValueError(f"Column {column_id!r} not found on this board")

        # Guard the WIP invariant: never remove the last WIP column
        if col.is_wip:
            wip_columns = [c for c in self.columns if c.is_wip]
            if len(wip_columns) == 1:
                raise ValueError(
                    "Cannot remove the last WIP column — the board must always have one"
                )

        if col.cards and not force:
            raise ValueError(
                f"Column {col.name!r} still has {len(col.cards)} card(s). "
                "Move them first or pass force=True to delete them along with the column."
            )

        self.columns.remove(col)
