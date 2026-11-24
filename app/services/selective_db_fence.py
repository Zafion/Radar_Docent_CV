"""Transactional authorization row for participating selective writers.

Schema and provisioning are deliberately limited to isolated tests. Revocation
must use the same row; legacy processes do not participate in this protocol.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class TransactionAuthorization:
    scope: str
    generation: int
    token: str

    def __post_init__(self):
        if (not isinstance(self.scope,str) or not self.scope or type(self.generation) is not int
                or self.generation<1 or not isinstance(self.token,str) or not self.token):
            raise ValueError('invalid_transaction_authorization')

    def acquire(self, connection):
        # The row lock belongs to the caller's transaction and is retained by
        # PostgreSQL until that transaction commits or rolls back.
        row=connection.execute('''SELECT generation,token,active FROM selective_cycle_authority
            WHERE scope=%s FOR SHARE''',(self.scope,)).fetchone()
        if row is None or not row['active'] or row['generation']!=self.generation or row['token']!=self.token:
            raise PermissionError('transaction_authorization_revoked')


def revoke_authorization(connection, authorization):
    """Revoke under the same row lock; caller owns commit/rollback."""
    row=connection.execute('''SELECT generation,token,active FROM selective_cycle_authority
        WHERE scope=%s FOR UPDATE''',(authorization.scope,)).fetchone()
    if row is None or not row['active'] or row['generation']!=authorization.generation or row['token']!=authorization.token:
        raise PermissionError('transaction_authorization_revoked')
    connection.execute('''UPDATE selective_cycle_authority SET active=false,generation=generation+1
        WHERE scope=%s''',(authorization.scope,))


def renew_authorization(connection, revoked, new_token):
    """Issue the next generation only after the prior one was revoked."""
    if not isinstance(new_token,str) or not new_token or new_token==revoked.token:
        raise ValueError('new_token_required')
    row=connection.execute('''SELECT generation,token,active FROM selective_cycle_authority
        WHERE scope=%s FOR UPDATE''',(revoked.scope,)).fetchone()
    if (row is None or row['active'] or row['generation']!=revoked.generation+1
            or row['token']!=revoked.token):
        raise PermissionError('previous_generation_not_revoked')
    connection.execute('UPDATE selective_cycle_authority SET token=%s,active=true WHERE scope=%s',
                       (new_token,revoked.scope))
    return TransactionAuthorization(revoked.scope,row['generation'],new_token)
